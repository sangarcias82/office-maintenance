from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.exceptions import BusinessError
from app.models import Location, MaintenanceType, Priority, RequestStatus, Role


def list_locations(db: Session, *, company_id: int, active_only: bool = True) -> list[Location]:
    """Locations of one company (catalogs like priority/status stay global)."""
    stmt = select(Location).where(Location.company_id == company_id).order_by(Location.name)
    if active_only:
        stmt = stmt.where(Location.active.is_(True))
    return list(db.scalars(stmt))


def list_priorities(db: Session) -> list[Priority]:
    return list(db.scalars(select(Priority).order_by(Priority.level)))


def list_statuses(db: Session) -> list[RequestStatus]:
    return list(db.scalars(select(RequestStatus).order_by(RequestStatus.id)))


def list_maintenance_types(db: Session) -> list[MaintenanceType]:
    return list(db.scalars(select(MaintenanceType).order_by(MaintenanceType.id)))


def list_roles(db: Session) -> list[Role]:
    return list(db.scalars(select(Role).order_by(Role.id)))


def status_id_by_code(db: Session, code: str) -> int:
    value = db.scalar(select(RequestStatus.id).where(RequestStatus.code == code))
    if value is None:
        raise BusinessError(f"{code} status is not configured.", "error.statusMissing")
    return value


def status_code_by_id(db: Session, status_id: int) -> str:
    value = db.scalar(select(RequestStatus.code).where(RequestStatus.id == status_id))
    if value is None:
        raise BusinessError("Invalid service status.", "error.invalidStatus")
    return value


def loggable_statuses(db: Session) -> list[RequestStatus]:
    """Statuses a technician may set after attendance (not PENDING)."""
    return [
        s
        for s in list_statuses(db)
        if s.code in ("IN_PROGRESS", "COMPLETED", "CANCELLED")
    ]
