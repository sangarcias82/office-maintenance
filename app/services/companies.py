from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Company


def list_active(db: Session) -> list[Company]:
    return list(
        db.scalars(select(Company).where(Company.active.is_(True)).order_by(Company.display_name))
    )


def get_active_by_slug(db: Session, slug: Optional[str]) -> Optional[Company]:
    key = (slug or "").strip()
    if not key:
        return None
    return db.scalar(select(Company).where(Company.slug == key, Company.active.is_(True)))
