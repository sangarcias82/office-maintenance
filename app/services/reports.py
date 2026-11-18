from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.exceptions import BusinessError
from app.models import AppUser, Location, MaintenanceRecord, MaintenanceType, ServiceRequest


@dataclass
class Indicators:
    total: int
    pending: int
    in_progress: int
    completed: int
    cancelled: int


@dataclass
class ReportRow:
    ticket_number: str
    location: str
    technician: str
    reported_at: datetime
    attended_at: Optional[datetime]
    maintenance_type: str
    status: str
    status_code: str


def _parse_day_start(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    d = date.fromisoformat(value)
    return datetime.combine(d, time.min)


def _parse_day_end(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    d = date.fromisoformat(value)
    return datetime.combine(d, time.max)


def filtered_requests(
    db: Session,
    *,
    actor: AppUser,
    from_date: Optional[str] = None,
    to_date: Optional[str] = None,
    maintenance_type_id: Optional[int] = None,
    location_id: Optional[int] = None,
) -> list[ServiceRequest]:
    if not actor.can_view_reports():
        raise BusinessError("Only area managers can view reports.", "error.onlyManagersReport")

    stmt = (
        select(ServiceRequest)
        .options(
            joinedload(ServiceRequest.location),
            joinedload(ServiceRequest.priority),
            joinedload(ServiceRequest.maintenance_type),
            joinedload(ServiceRequest.status),
            joinedload(ServiceRequest.assigned_technician),
            joinedload(ServiceRequest.records),
        )
        .order_by(ServiceRequest.reported_at.desc())
    )
    start = _parse_day_start(from_date)
    end = _parse_day_end(to_date)
    if start:
        stmt = stmt.where(ServiceRequest.reported_at >= start)
    if end:
        stmt = stmt.where(ServiceRequest.reported_at <= end)
    if maintenance_type_id:
        stmt = stmt.where(ServiceRequest.maintenance_type_id == maintenance_type_id)
    if location_id:
        stmt = stmt.where(ServiceRequest.location_id == location_id)
    return list(db.scalars(stmt).unique())


def build_indicators(rows: list[ServiceRequest]) -> Indicators:
    pending = in_progress = completed = cancelled = 0
    for r in rows:
        code = r.status.code
        if code == "PENDING":
            pending += 1
        elif code == "IN_PROGRESS":
            in_progress += 1
        elif code == "COMPLETED":
            completed += 1
        elif code == "CANCELLED":
            cancelled += 1
    return Indicators(
        total=len(rows),
        pending=pending,
        in_progress=in_progress,
        completed=completed,
        cancelled=cancelled,
    )


def build_report_rows(rows: list[ServiceRequest], unassigned_label: str = "Unassigned") -> list[ReportRow]:
    out: list[ReportRow] = []
    for r in rows:
        latest = None
        if r.records:
            latest = max(r.records, key=lambda m: m.attended_at or datetime.min)
        tech = r.assigned_technician.full_name() if r.assigned_technician else unassigned_label
        out.append(
            ReportRow(
                ticket_number=r.ticket_number,
                location=r.location.name if r.location else "",
                technician=tech,
                reported_at=r.reported_at,
                attended_at=latest.attended_at if latest else None,
                maintenance_type=r.maintenance_type.name if r.maintenance_type else "",
                status=r.status.name if r.status else "",
                status_code=r.status.code if r.status else "",
            )
        )
    return out


def chart_by_status(rows: list[ServiceRequest]) -> tuple[list[str], list[int]]:
    counts: dict[str, int] = {}
    for r in rows:
        label = r.status.name
        counts[label] = counts.get(label, 0) + 1
    labels = list(counts.keys())
    values = [counts[k] for k in labels]
    return labels, values


def chart_by_type(rows: list[ServiceRequest]) -> tuple[list[str], list[int]]:
    counts: dict[str, int] = {}
    for r in rows:
        label = r.maintenance_type.name
        counts[label] = counts.get(label, 0) + 1
    labels = list(counts.keys())
    values = [counts[k] for k in labels]
    return labels, values


def count_all(db: Session) -> int:
    return db.scalar(select(func.count()).select_from(ServiceRequest)) or 0
