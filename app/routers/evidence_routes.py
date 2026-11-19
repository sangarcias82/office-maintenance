"""Authenticated photo evidence downloads (replaces the old public /uploads mount)."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse, PlainTextResponse, Response
from sqlalchemy.orm import Session

from app.auth import require_user
from app.db import get_db
from app.models import AppUser
from app.services import evidence as evidence_service

router = APIRouter(tags=["evidence"])


@router.get("/evidence/{photo_id:int}", name="evidence_file")
def evidence_file(
    photo_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),  # anonymous -> redirect to /login
) -> Response:
    """Serve a photo only if its request is visible to the user (same rule as the request list).

    Another company's photo, a ticket the role may not see, an unknown id, and a
    missing file all get the same 404, so existence is not revealed.
    """
    photo = evidence_service.get_visible_photo(db, actor=user, photo_id=photo_id)
    found = evidence_service.file_for(photo) if photo is not None else None
    if found is None:
        return PlainTextResponse("Not found", status_code=404)
    path, content_type = found
    return FileResponse(
        path,
        media_type=content_type,
        filename=photo.file_name,
        content_disposition_type="inline",
        headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"},
    )
