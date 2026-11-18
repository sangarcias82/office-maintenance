from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import Forbidden, get_lang, require_user
from app.db import get_db
from app.deps import flash, render
from app.exceptions import BusinessError
from app.i18n import t
from app.models import AppUser
from app.services import catalog, requests as request_service, users

router = APIRouter(prefix="/requests", tags=["requests"])


@router.get("/new")
def new_request_form(
    request: Request,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),
):
    if not user.can_create_request():
        raise Forbidden(t(get_lang(request), "forbidden"))
    return render(
        request,
        "requests/new.html",
        current_user=user,
        title_key="request.title",
        locations=catalog.list_locations(db),
        priorities=catalog.list_priorities(db),
        maintenance_types=catalog.list_maintenance_types(db),
    )


@router.post("")
@router.post("/")
def create_request(
    request: Request,
    location_id: int = Form(...),
    priority_id: int = Form(...),
    maintenance_type_id: int = Form(...),
    problem_description: str = Form(...),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),
):
    try:
        created = request_service.create_request(
            db,
            actor=user,
            location_id=location_id,
            problem_description=problem_description,
            priority_id=priority_id,
            maintenance_type_id=maintenance_type_id,
        )
    except BusinessError as exc:
        flash(request, exc.message, "error")
        return render(
            request,
            "requests/new.html",
            current_user=user,
            title_key="request.title",
            locations=catalog.list_locations(db),
            priorities=catalog.list_priorities(db),
            maintenance_types=catalog.list_maintenance_types(db),
            form={
                "location_id": location_id,
                "priority_id": priority_id,
                "maintenance_type_id": maintenance_type_id,
                "problem_description": problem_description,
            },
        )
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
        technicians=users.list_technicians(db) if user.is_area_manager() else [],
    )


@router.post("/assign")
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
        flash(request, "Technician assigned.", "ok")
    except BusinessError as exc:
        flash(request, exc.message, "error")
    return RedirectResponse("/requests", status_code=303)
