from __future__ import annotations

from typing import Optional

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.exceptions import BusinessError
from app.models import AppUser, Role


def require_by_id(db: Session, user_id: int, *, company_id: Optional[int] = None) -> AppUser:
    """Load a user; with ``company_id``, users of other companies count as not found."""
    stmt = select(AppUser).options(joinedload(AppUser.role)).where(AppUser.id == user_id)
    if company_id is not None:
        stmt = stmt.where(AppUser.company_id == company_id)
    user = db.scalar(stmt)
    if user is None:
        raise BusinessError("User not found.", "error.userNotFound")
    return user


def list_technicians(db: Session, *, company_id: int) -> list[AppUser]:
    return list(
        db.scalars(
            select(AppUser)
            .join(Role)
            .options(joinedload(AppUser.role))
            .where(
                Role.code == "TECHNICIAN",
                AppUser.active.is_(True),
                AppUser.company_id == company_id,
            )
            .order_by(AppUser.first_name, AppUser.last_name)
        )
    )
