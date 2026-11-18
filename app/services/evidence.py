from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from fastapi import UploadFile

from app.config import get_settings

ALLOWED_CONTENT_TYPES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/gif": ".gif",
    "image/webp": ".webp",
}
MAX_BYTES = 5 * 1024 * 1024


@dataclass
class StoredEvidence:
    file_name: str
    public_path: str
    absolute_path: Path


def store_evidence(request_id: int, upload: Optional[UploadFile]) -> Optional[StoredEvidence]:
    if upload is None or not upload.filename:
        return None
    content_type = (upload.content_type or "").split(";")[0].strip().lower()
    if content_type not in ALLOWED_CONTENT_TYPES:
        # Ignore unsupported types quietly for the form UX; callers may flash later.
        return None

    raw = upload.file.read(MAX_BYTES + 1)
    if not raw or len(raw) > MAX_BYTES:
        return None

    ext = ALLOWED_CONTENT_TYPES[content_type]
    safe_name = f"{uuid.uuid4().hex}-evidence{ext}"
    root = Path(get_settings().upload_dir)
    dest_dir = root / "evidence" / str(request_id)
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / safe_name
    dest.write_bytes(raw)

    public = f"/uploads/evidence/{request_id}/{safe_name}"
    return StoredEvidence(file_name=safe_name, public_path=public, absolute_path=dest)
