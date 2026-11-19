from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import Forbidden, get_lang, require_user
from app.csrf import require_csrf
from app.db import get_db
from app.deps import flash, render
from app.exceptions import BusinessError
from app.i18n import error_text, t
from app.models import AppUser
from app.services import catalog, requests as request_service, users

router = APIRouter(prefix="/requests", tags=["requests"])


def _int_or_none(value: Optional[str]) -> Optional[int]:
    """Form ids arrive as text; anything non-numeric becomes None (a form error, not a 422)."""
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None


def _form_page(request: Request, db: Session, user: AppUser, form: Optional[dict] = None):
    return render(
        request,
        "requests/new.html",
        current_user=user,
        title_key="request.title",
        locations=catalog.list_locations(db, company_id=user.company_id),
        priorities=catalog.list_priorities(db),
        maintenance_types=catalog.list_maintenance_types(db),
        form=form,
    )


@router.get("/new")
def new_request_form(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),
):
    if not user.can_create_request():
        raise Forbidden(t(get_lang(request), "forbidden"))
    return _form_page(request, db, user)


@router.post("", dependencies=[Depends(require_csrf)])
@router.post("/", dependencies=[Depends(require_csrf)])
def create_request(
    request: Request,
    location_id: str = Form(""),
    priority_id: str = Form(""),
    maintenance_type_id: str = Form(""),
    problem_description: str = Form(""),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),
):
    form = {
        "location_id": _int_or_none(location_id),
        "priority_id": _int_or_none(priority_id),
        "maintenance_type_id": _int_or_none(maintenance_type_id),
        "problem_description": problem_description,
    }
    try:
        created = request_service.create_request(db, actor=user, **form)
    except BusinessError as exc:
        flash(request, error_text(get_lang(request), exc), "error")
        return _form_page(request, db, user, form)
    flash(request, t(get_lang(request), "request.created", ticket=created.ticket_number), "ok")
    return RedirectResponse("/requests", status_code=303)


@router.get("")
@router.get("/")
def list_requests(
    request: Request,
    q: Optional[str] = None,
    statusId: Optional[int] = None,
    maintenanceTypeId: Optional[int] = None,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),
):
    rows = request_service.list_requests(
        db,
        actor=user,
        q=q,
        status_id=statusId,
        maintenance_type_id=maintenanceTypeId,
    )
    return render(
        request,
        "requests/list.html",
        current_user=user,
        title_key="list.title",
        requests=rows,
        q=q or "",
        status_id=statusId,
        maintenance_type_id=maintenanceTypeId,
        statuses=catalog.list_statuses(db),
        maintenance_types=catalog.list_maintenance_types(db),
        technicians=users.list_technicians(db, company_id=user.company_id) if user.is_area_manager() else [],
    )


@router.get("/{request_id:int}")
def request_detail(
    request: Request,
    request_id: int,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),
):
    """Ticket detail with work log and photo evidence; same visibility as the list."""
    try:
        ticket = request_service.get_visible(db, actor=user, request_id=request_id)
    except BusinessError as exc:
        flash(request, error_text(get_lang(request), exc), "error")
        return RedirectResponse("/requests", status_code=303)
    return render(
        request,
        "requests/detail.html",
        current_user=user,
        title_key="detail.title",
        ticket=ticket,
        records=sorted(ticket.records, key=lambda r: r.attended_at),
    )


@router.post("/assign", dependencies=[Depends(require_csrf)])
def assign_technician(
    request: Request,
    requestId: int = Form(...),
    technicianId: int = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),
):
    try:
        request_service.assign_technician(
            db, request_id=requestId, technician_id=technicianId, actor=user
        )
        flash(request, t(get_lang(request), "list.assigned"), "ok")
    except BusinessError as exc:
        flash(request, error_text(get_lang(request), exc), "error")
    return RedirectResponse("/requests", status_code=303)
