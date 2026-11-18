from __future__ import annotations

from datetime import datetime
from typing import Optional

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session, joinedload

from app.exceptions import BusinessError
from app.models import AppUser, Location, MaintenanceType, Priority, RequestStatus, ServiceRequest
from app.services import catalog, users


def next_ticket_number(db: Session, year: Optional[int] = None) -> str:
    year = year or datetime.utcnow().year
    prefix = f"REQ-{year}-"
    last = db.scalar(
        select(ServiceRequest.ticket_number)
        .where(ServiceRequest.ticket_number.like(f"{prefix}%"))
        .order_by(ServiceRequest.ticket_number.desc())
        .limit(1)
    )
    seq = 1
    if last:
        try:
            seq = int(last.rsplit("-", 1)[-1]) + 1
        except ValueError:
            seq = 1
    return f"{prefix}{seq:04d}"


def create_request(
    db: Session,
    *,
    actor: AppUser,
    location_id: int,
    problem_description: str,
    priority_id: int,
    maintenance_type_id: int,
) -> ServiceRequest:
    if not actor.can_create_request():
        raise BusinessError(
            "Only employees (or area managers) can register service requests.",
            "error.onlyRequesters",
        )
    desc = (problem_description or "").strip()
    if not desc:
        raise BusinessError("Problem description is required.", "error.descriptionRequired")
    if not location_id or not priority_id or not maintenance_type_id:
        raise BusinessError("Location, priority, and type are required.", "error.catalogRequired")

    pending_id = catalog.status_id_by_code(db, "PENDING")
    ticket = next_ticket_number(db)
    now = datetime.utcnow()
    req = ServiceRequest(
        ticket_number=ticket,
        reported_at=now,
        location_id=location_id,
        problem_description=desc,
        priority_id=priority_id,
        maintenance_type_id=maintenance_type_id,
        status_id=pending_id,
        requester_id=actor.id,
        updated_at=now,
    )
    db.add(req)
    db.commit()
    db.refresh(req)
    return require_by_id(db, req.id)


def require_by_id(db: Session, request_id: int) -> ServiceRequest:
    req = db.scalar(
        select(ServiceRequest)
        .options(
            joinedload(ServiceRequest.location),
            joinedload(ServiceRequest.priority),
            joinedload(ServiceRequest.maintenance_type),
            joinedload(ServiceRequest.status),
            joinedload(ServiceRequest.requester).joinedload(AppUser.role),
            joinedload(ServiceRequest.assigned_technician).joinedload(AppUser.role),
        )
        .where(ServiceRequest.id == request_id)
    )
    if req is None:
        raise BusinessError("Service request not found.", "error.requestNotFound")
    return req


def list_requests(
    db: Session,
    *,
    actor: AppUser,
    q: Optional[str] = None,
    status_id: Optional[int] = None,
    maintenance_type_id: Optional[int] = None,
) -> list[ServiceRequest]:
    stmt = (
        select(ServiceRequest)
        .options(
            joinedload(ServiceRequest.location),
            joinedload(ServiceRequest.priority),
            joinedload(ServiceRequest.maintenance_type),
            joinedload(ServiceRequest.status),
            joinedload(ServiceRequest.assigned_technician),
        )
        .order_by(ServiceRequest.reported_at.desc())
    )
    if actor.is_employee():
        stmt = stmt.where(ServiceRequest.requester_id == actor.id)
    elif actor.is_technician():
        stmt = stmt.where(ServiceRequest.assigned_technician_id == actor.id)

    if status_id:
        stmt = stmt.where(ServiceRequest.status_id == status_id)
    if maintenance_type_id:
        stmt = stmt.where(ServiceRequest.maintenance_type_id == maintenance_type_id)
    if q and q.strip():
        term = f"%{q.strip().lower()}%"
        stmt = stmt.join(Location).where(
            or_(
                func.lower(ServiceRequest.ticket_number).like(term),
                func.lower(ServiceRequest.problem_description).like(term),
                func.lower(Location.name).like(term),
                func.lower(Location.code).like(term),
            )
        )
    return list(db.scalars(stmt).unique())


def list_assignable_for_technician(db: Session, technician: AppUser) -> list[ServiceRequest]:
    if not technician.is_technician():
        raise BusinessError(
            "Only technicians can open maintenance work forms.",
            "error.onlyTechniciansForm",
        )
    return list_requests(db, actor=technician)


def assign_technician(
    db: Session, *, request_id: int, technician_id: int, actor: AppUser
) -> ServiceRequest:
    if not actor.is_area_manager():
        raise BusinessError("Only area managers can assign technicians.", "error.onlyManagersAssign")

    req = require_by_id(db, request_id)
    if req.status.code in ("COMPLETED", "CANCELLED"):
        raise BusinessError("Cannot assign a technician to a closed request.", "error.assignClosed")

    technician = users.require_by_id(db, technician_id)
    if not technician.is_technician():
        raise BusinessError(
            "Assigned user must have the TECHNICIAN role.",
            "error.assigneeMustBeTechnician",
        )

    in_progress_id = catalog.status_id_by_code(db, "IN_PROGRESS")
    now = datetime.utcnow()
    req.assigned_technician_id = technician.id
    req.assigned_at = now
    req.status_id = in_progress_id
    req.updated_at = now
    db.commit()
    return require_by_id(db, request_id)


def update_status(db: Session, request_id: int, status_id: int) -> None:
    req = db.get(ServiceRequest, request_id)
    if req is None:
        raise BusinessError("Service request not found.", "error.requestNotFound")
    req.status_id = status_id
    req.updated_at = datetime.utcnow()
