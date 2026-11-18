from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.exceptions import BusinessError
from app.models import AppUser, Role


def require_by_id(db: Session, user_id: int) -> AppUser:
    user = db.scalar(
        select(AppUser).options(joinedload(AppUser.role)).where(AppUser.id == user_id)
    )
    if user is None:
        raise BusinessError("User not found.", "error.userNotFound")
    return user


def list_technicians(db: Session) -> list[AppUser]:
    return list(
        db.scalars(
            select(AppUser)
            .join(Role)
            .options(joinedload(AppUser.role))
            .where(Role.code == "TECHNICIAN", AppUser.active.is_(True))
            .order_by(AppUser.first_name, AppUser.last_name)
        )
    )
