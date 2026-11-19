"""Photo evidence: saving uploads and serving them only to people who may see the ticket.

Files live under ``settings.upload_dir`` and are NOT publicly mounted. The DB stores
a path relative to that folder; ``GET /evidence/{photo_id}`` (routers/evidence_routes.py)
serves a file only after the same company/role visibility check as the request list.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.exceptions import BusinessError
from app.models import AppUser, MaintenanceRecord, PhotoEvidence, ServiceRequest
from app.services import requests as request_service

logger = logging.getLogger(__name__)

# Allowlist used when saving (unchanged) and when serving (content type by extension).
ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
CONTENT_TYPE_BY_EXTENSION = {ext: ctype for ctype, ext in ALLOWED_CONTENT_TYPES.items()}
MAX_BYTES = 5 * 1024 * 1024


@dataclass
class PreparedEvidence:
    """An upload that passed every check, held in memory until the record is saved."""

    content: bytes
    extension: str


@dataclass
class StoredEvidence:
    file_name: str
    relative_path: str
    absolute_path: Path


def upload_root() -> Path:
    return Path(get_settings().upload_dir).resolve()


def evidence_relative_path(company_id: int, request_id: int, file_name: str) -> str:
    return f"evidence/{company_id}/{request_id}/{file_name}"


def _looks_like(content_type: str, data: bytes) -> bool:
    """The file's first bytes must match the declared image type (not just the header)."""
    if content_type == "image/jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if content_type == "image/png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if content_type == "image/gif":
        return data[:6] in (b"GIF87a", b"GIF89a")
    return data[:4] == b"RIFF" and data[8:12] == b"WEBP"  # image/webp


def prepare_evidence(upload: Optional[UploadFile]) -> Optional[PreparedEvidence]:
    """Validate an optional photo before anything is written.

    No file chosen -> None. Otherwise the same allowlist as always (JPEG, PNG,
    GIF, WebP, at most 5 MB) and the content must really be that image type.
    Any failure raises BusinessError, so the work record is not saved either.
    """
    if upload is None or not upload.filename:
        return None
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        raise BusinessError("Photo type not allowed.", "error.evidenceType")
    raw = upload.file.read(MAX_BYTES + 1)
    if not raw:
        raise BusinessError("Photo file is empty.", "error.evidenceEmpty")
    if len(raw) > MAX_BYTES:
        raise BusinessError("Photo is larger than 5 MB.", "error.evidenceTooLarge")
    if not _looks_like(content_type, raw):
        raise BusinessError("Photo content does not match its type.", "error.evidenceType")
    return PreparedEvidence(content=raw, extension=ALLOWED_CONTENT_TYPES[content_type])


def write_evidence(company_id: int, request_id: int, prepared: PreparedEvidence) -> StoredEvidence:
    """Write a prepared photo under a server-generated name. Raises OSError on disk problems."""
    safe_name = f"{uuid.uuid4().hex}-evidence{prepared.extension}"
    relative = evidence_relative_path(company_id, request_id, safe_name)
    dest = upload_root() / relative
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(prepared.content)
    return StoredEvidence(file_name=safe_name, relative_path=relative, absolute_path=dest)


def get_visible_photo(db: Session, *, actor: AppUser, photo_id: int) -> Optional[PhotoEvidence]:
    """The photo if its request is visible to ``actor`` (company + role rules), else None."""
    return db.scalar(
        select(PhotoEvidence)
        .join(MaintenanceRecord, PhotoEvidence.record_id == MaintenanceRecord.id)
        .join(ServiceRequest, MaintenanceRecord.request_id == ServiceRequest.id)
        .where(PhotoEvidence.id == photo_id, request_service.visible_to(actor))
    )


def file_for(photo: PhotoEvidence) -> Optional[tuple[Path, str]]:
    """(absolute path, content type) of an allowlisted file inside the upload folder, else None."""
    root = upload_root()
    path = (root / photo.relative_path).resolve()
    if not path.is_relative_to(root):
        logger.warning("Evidence %s points outside the upload folder", photo.id)
        return None
    content_type = CONTENT_TYPE_BY_EXTENSION.get(path.suffix.lower())
    if content_type is None:
        return None
    if not path.is_file():
        logger.warning("Evidence file missing for photo %s", photo.id)
        return None
    return path, content_type
