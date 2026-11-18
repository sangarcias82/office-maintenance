from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from app.auth import require_user
from app.db import get_db
from app.deps import render
from app.models import AppUser

router = APIRouter(tags=["web"])


@router.get("/")
def home(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),
):
    return render(request, "home.html", current_user=user, title_key="home.title")
