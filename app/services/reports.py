from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime, time
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.orm import Session, joinedload

from app.exceptions import BusinessError
from app.models import AppUser, Location, MaintenanceRecord, MaintenanceType, ServiceRequest


# label(kind, code, fallback) -> display text; routes pass the locale-aware i18n label.
LabelFn = Callable[[str, str, str], str]


def _db_name(_kind: str, _code: str, fallback: str) -> str:
    return fallback


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


def parse_filter_date(value: Optional[str]) -> Optional[date]:
    """Parse a YYYY-MM-DD filter value; blank means no filter."""
    if not value or not value.strip():
        return None
    try:
        return date.fromisoformat(value.strip())
    except ValueError:
        raise BusinessError("Dates must use the YYYY-MM-DD format.", "error.invalidDate") from None


def _parse_day_start(value: Optional[str]) -> Optional[datetime]:
    d = parse_filter_date(value)
    return datetime.combine(d, time.min) if d else None


def _parse_day_end(value: Optional[str]) -> Optional[datetime]:
    d = parse_filter_date(value)
    return datetime.combine(d, time.max) if d else None


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

    start = _parse_day_start(from_date)  # raises BusinessError(error.invalidDate) on bad input
    end = _parse_day_end(to_date)
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
        .where(ServiceRequest.company_id == actor.company_id)
        .order_by(ServiceRequest.reported_at.desc())
    )
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


def build_report_rows(
    rows: list[ServiceRequest],
    unassigned_label: str = "Unassigned",
    label: LabelFn = _db_name,
) -> list[ReportRow]:
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
                maintenance_type=(
                    label("type", r.maintenance_type.code, r.maintenance_type.name)
                    if r.maintenance_type
                    else ""
                ),
                status=label("status", r.status.code, r.status.name) if r.status else "",
                status_code=r.status.code if r.status else "",
            )
        )
    return out


def chart_by_status(rows: list[ServiceRequest], label: LabelFn = _db_name) -> tuple[list[str], list[int]]:
    counts: dict[str, int] = {}
    for r in rows:
        text = label("status", r.status.code, r.status.name)
        counts[text] = counts.get(text, 0) + 1
    labels = list(counts.keys())
    values = [counts[k] for k in labels]
    return labels, values


def chart_by_type(rows: list[ServiceRequest], label: LabelFn = _db_name) -> tuple[list[str], list[int]]:
    counts: dict[str, int] = {}
    for r in rows:
        text = label("type", r.maintenance_type.code, r.maintenance_type.name)
        counts[text] = counts.get(text, 0) + 1
    labels = list(counts.keys())
    values = [counts[k] for k in labels]
    return labels, values


def count_all(db: Session, *, company_id: int) -> int:
    return (
        db.scalar(
            select(func.count()).select_from(ServiceRequest).where(ServiceRequest.company_id == company_id)
        )
        or 0
    )
