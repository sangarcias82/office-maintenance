"""Service requests. Every read and write is scoped to the actor's company."""

from __future__ import annotations

from typing import Optional

from sqlalchemy import and_, false, func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, joinedload

from app.exceptions import BusinessError
from app.models import (
    AppUser,
    Location,
    MaintenanceRecord,
    MaintenanceType,
    Priority,
    ServiceRequest,
    TicketCounter,
)
from app.services import catalog, users
from app.timeutil import utcnow

TICKET_ALLOCATION_ATTEMPTS = 3


# --- ticket numbers -----------------------------------------------------------


def _ticket(year: int, number: int) -> str:
    return f"REQ-{year}-{number:04d}"


def highest_ticket_number(db: Session, company_id: int, year: int) -> int:
    """Highest well-formed REQ-<year>-NNNN sequence already used by the company (0 if none).

    Malformed suffixes are ignored, and the comparison is numeric, so
    REQ-2026-10000 counts as higher than REQ-2026-9999.
    """
    prefix = f"REQ-{year}-"
    highest = 0
    for ticket in db.scalars(
        select(ServiceRequest.ticket_number).where(
            ServiceRequest.company_id == company_id,
            ServiceRequest.ticket_number.like(f"{prefix}%"),
        )
    ):
        suffix = ticket[len(prefix):]
        if suffix.isascii() and suffix.isdigit():
            highest = max(highest, int(suffix))
    return highest


def next_ticket_number(db: Session, company_id: int, year: Optional[int] = None) -> str:
    """Preview of the next number (read-only; allocation uses allocate_ticket_number)."""
    year = year or utcnow().year
    counter = db.get(TicketCounter, (company_id, year))
    last = max(counter.last_number if counter else 0, highest_ticket_number(db, company_id, year))
    return _ticket(year, last + 1)


def allocate_ticket_number(db: Session, company_id: int, year: Optional[int] = None) -> str:
    """Take the next number from the company/year counter inside the caller's transaction.

    The single UPDATE ... SET last_number = last_number + 1 takes the write lock,
    so a concurrent create waits and then gets the following number instead of
    computing the same max+1. The first ticket of a year creates the counter row,
    starting after any tickets that already exist.
    """
    year = year or utcnow().year
    bumped = db.execute(
        update(TicketCounter)
        .where(TicketCounter.company_id == company_id, TicketCounter.year == year)
        .values(last_number=TicketCounter.last_number + 1)
        .execution_options(synchronize_session=False)
    ).rowcount
    if not bumped:
        db.add(
            TicketCounter(
                company_id=company_id,
                year=year,
                last_number=highest_ticket_number(db, company_id, year) + 1,
            )
        )
        db.flush()
    number = db.scalar(
        select(TicketCounter.last_number).where(
            TicketCounter.company_id == company_id, TicketCounter.year == year
        )
    )
    return _ticket(year, number)


def _resync_counter(db: Session, company_id: int, year: int) -> None:
    """Move the counter past tickets created outside it (imports, manual edits)."""
    counter = db.get(TicketCounter, (company_id, year))
    highest = highest_ticket_number(db, company_id, year)
    if counter is None:
        db.add(TicketCounter(company_id=company_id, year=year, last_number=highest))
    elif counter.last_number < highest:
        counter.last_number = highest
    try:
        db.commit()
    except IntegrityError:  # another create inserted the counter row first; it will be bumped
        db.rollback()


def _ticket_taken(db: Session, company_id: int, ticket: str) -> bool:
    return (
        db.scalar(
            select(ServiceRequest.id).where(
                ServiceRequest.company_id == company_id, ServiceRequest.ticket_number == ticket
            )
        )
        is not None
    )


# --- create -------------------------------------------------------------------


def _require_location(db: Session, location_id: Optional[int], company_id: int) -> Location:
    location = db.get(Location, location_id) if location_id else None
    if location is None or location.company_id != company_id:
        # Another company's location is reported exactly like a missing one.
        raise BusinessError("Choose a valid location.", "error.locationInvalid")
    if not location.active:
        raise BusinessError("That location is no longer active.", "error.locationInactive")
    return location


def create_request(
    db: Session,
    *,
    actor: AppUser,
    location_id: Optional[int],
    problem_description: str,
    priority_id: Optional[int],
    maintenance_type_id: Optional[int],
) -> ServiceRequest:
    if not actor.can_create_request():
        raise BusinessError(
            "Only employees (or area managers) can register service requests.",
            "error.onlyRequesters",
        )
    desc = (problem_description or "").strip()
    if not desc:
        raise BusinessError("Problem description is required.", "error.descriptionRequired")
    company_id = actor.company_id
    _require_location(db, location_id, company_id)
    if not priority_id or db.get(Priority, priority_id) is None:
        raise BusinessError("Choose a valid priority.", "error.priorityInvalid")
    if not maintenance_type_id or db.get(MaintenanceType, maintenance_type_id) is None:
        raise BusinessError("Choose a valid maintenance type.", "error.typeInvalid")

    pending_id = catalog.status_id_by_code(db, "PENDING")
    for _attempt in range(TICKET_ALLOCATION_ATTEMPTS):
        now = utcnow()
        ticket: Optional[str] = None
        try:
            ticket = allocate_ticket_number(db, company_id, now.year)
            req = ServiceRequest(
                company_id=company_id,
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
        except IntegrityError:
            db.rollback()
            if ticket is not None and not _ticket_taken(db, company_id, ticket):
                # Not a numbering problem: report it instead of retrying blindly.
                raise BusinessError(
                    "The request could not be saved. Please try again.", "error.requestNotSaved"
                ) from None
            # The counter row was created concurrently (ticket is None), or the number
            # is used by a ticket created outside the counter: catch up and retry.
            _resync_counter(db, company_id, now.year)
            continue
        return require_by_id(db, req.id, company_id=company_id)
    raise BusinessError("Could not allocate a ticket number. Please submit again.", "error.ticketConflict")


# --- read ---------------------------------------------------------------------


def visible_to(actor: AppUser):
    """SQL condition: requests ``actor`` may see. One rule for list, detail and evidence.

    Always the actor's company; employees see the requests they reported,
    technicians the ones assigned to them, area managers all of the company.
    Any other role sees nothing.
    """
    same_company = ServiceRequest.company_id == actor.company_id
    if actor.is_area_manager():
        return same_company
    if actor.is_employee():
        return and_(same_company, ServiceRequest.requester_id == actor.id)
    if actor.is_technician():
        return and_(same_company, ServiceRequest.assigned_technician_id == actor.id)
    return false()


def get_visible(db: Session, *, actor: AppUser, request_id: int) -> ServiceRequest:
    """Request detail (with work records, materials, photos) if visible to ``actor``."""
    req = db.scalar(
        select(ServiceRequest)
        .options(
            joinedload(ServiceRequest.location),
            joinedload(ServiceRequest.priority),
            joinedload(ServiceRequest.maintenance_type),
            joinedload(ServiceRequest.status),
            joinedload(ServiceRequest.requester),
            joinedload(ServiceRequest.assigned_technician),
            joinedload(ServiceRequest.records).joinedload(MaintenanceRecord.materials),
            joinedload(ServiceRequest.records).joinedload(MaintenanceRecord.photos),
            joinedload(ServiceRequest.records).joinedload(MaintenanceRecord.technician),
            joinedload(ServiceRequest.records).joinedload(MaintenanceRecord.service_status),
        )
        .where(ServiceRequest.id == request_id, visible_to(actor))
    )
    if req is None:
        raise BusinessError("Service request not found.", "error.requestNotFound")
    return req


def require_by_id(db: Session, request_id: int, *, company_id: int) -> ServiceRequest:
    """A request of ``company_id``; other companies' requests count as not found."""
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
        .where(ServiceRequest.id == request_id, ServiceRequest.company_id == company_id)
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
        .where(visible_to(actor))
        .order_by(ServiceRequest.reported_at.desc())
    )

    if status_id:
        stmt = stmt.where(ServiceRequest.status_id == status_id)
    if maintenance_type_id:
        stmt = stmt.where(ServiceRequest.maintenance_type_id == maintenance_type_id)
    if q and q.strip():
        term = f"%{q.strip().lower()}%"
        stmt = stmt.join(Location, ServiceRequest.location_id == Location.id).where(
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
    # Only open tickets: work cannot be logged on COMPLETED/CANCELLED ones.
    return [r for r in list_requests(db, actor=technician) if not r.status.is_final]


# --- update -------------------------------------------------------------------


def assign_technician(
    db: Session, *, request_id: int, technician_id: int, actor: AppUser
) -> ServiceRequest:
    if not actor.is_area_manager():
        raise BusinessError("Only area managers can assign technicians.", "error.onlyManagersAssign")

    req = require_by_id(db, request_id, company_id=actor.company_id)
    if req.status.code in ("COMPLETED", "CANCELLED"):
        raise BusinessError("Cannot assign a technician to a closed request.", "error.assignClosed")

    technician = users.require_by_id(db, technician_id, company_id=actor.company_id)
    if not technician.is_technician():
        raise BusinessError(
            "Assigned user must have the TECHNICIAN role.",
            "error.assigneeMustBeTechnician",
        )

    in_progress_id = catalog.status_id_by_code(db, "IN_PROGRESS")
    now = utcnow()
    req.assigned_technician_id = technician.id
    req.assigned_at = now
    req.status_id = in_progress_id
    req.updated_at = now
    db.commit()
    return require_by_id(db, request_id, company_id=actor.company_id)


def update_status(db: Session, request_id: int, status_id: int, *, company_id: int) -> None:
    req = require_by_id(db, request_id, company_id=company_id)
    req.status_id = status_id
    req.updated_at = utcnow()
