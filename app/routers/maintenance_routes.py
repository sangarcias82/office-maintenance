from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import Forbidden, get_lang, require_user
from app.csrf import require_csrf
from app.db import get_db
from app.deps import flash, render
from app.exceptions import BusinessError
from app.i18n import error_text, t
from app.models import AppUser
from app.services import catalog, maintenance as maintenance_service, requests as request_service

router = APIRouter(prefix="/maintenance", tags=["maintenance"])


def _parse_materials(
    material_name: list[str],
    material_quantity: list[str],
    material_unit: list[str],
    material_notes: list[str],
) -> list[dict]:
    rows = []
    n = max(len(material_name), len(material_quantity), len(material_unit), len(material_notes), 0)
    for i in range(n):
        rows.append(
            {
                "name": material_name[i] if i < len(material_name) else "",
                "quantity": material_quantity[i] if i < len(material_quantity) else "",
                "unit": material_unit[i] if i < len(material_unit) else "ea",
                "notes": material_notes[i] if i < len(material_notes) else "",
            }
        )
    return rows


@router.get("/new")
def new_maintenance_form(
    request: Request,
    requestId: Optional[int] = None,
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),
):
    if not user.can_log_work():
        raise Forbidden(t(get_lang(request), "forbidden"))
    assigned = request_service.list_assignable_for_technician(db, user)
    selected = None
    if requestId:
        try:
            selected = request_service.require_by_id(db, requestId, company_id=user.company_id)
            if selected.assigned_technician_id != user.id or selected.status.is_final:
                selected = None
        except BusinessError:
            selected = None
    return render(
        request,
        "maintenance/new.html",
        current_user=user,
        title_key="maintenance.title",
        assigned_requests=assigned,
        selected_request=selected,
        statuses=catalog.loggable_statuses(db),
        material_rows=3,
    )


@router.post("", dependencies=[Depends(require_csrf)])
@router.post("/", dependencies=[Depends(require_csrf)])
def save_maintenance(
    request: Request,
    request_id: int = Form(...),
    work_performed: str = Form(...),
    notes: str = Form(""),
    service_status_id: int = Form(...),
    evidence_description: str = Form(""),
    material_name: list[str] = Form(default=[]),
    material_quantity: list[str] = Form(default=[]),
    material_unit: list[str] = Form(default=[]),
    material_notes: list[str] = Form(default=[]),
    evidenceFile: Optional[UploadFile] = File(None),
    db: Session = Depends(get_db),
    user: AppUser = Depends(require_user),
):
    materials = _parse_materials(material_name, material_quantity, material_unit, material_notes)
    try:
        maintenance_service.log_work(
            db,
            actor=user,
            request_id=request_id,
            work_performed=work_performed,
            notes=notes,
            service_status_id=service_status_id,
            materials=materials,
            evidence_file=evidenceFile,
            evidence_description=evidence_description,
        )
        flash(request, t(get_lang(request), "maintenance.saved"), "ok")
        return RedirectResponse("/requests", status_code=303)
    except BusinessError as exc:
        flash(request, error_text(get_lang(request), exc), "error")
        assigned = (
            request_service.list_assignable_for_technician(db, user)
            if user.is_technician()
            else []
        )
        selected = None
        try:
            selected = request_service.require_by_id(db, request_id, company_id=user.company_id)
            if selected.assigned_technician_id != user.id:
                selected = None
        except BusinessError:
            selected = None
        return render(
            request,
            "maintenance/new.html",
            current_user=user,
            title_key="maintenance.title",
            assigned_requests=assigned,
            selected_request=selected,
            statuses=catalog.loggable_statuses(db),
            material_rows=max(3, len(materials)),
            form={
                "request_id": request_id,
                "work_performed": work_performed,
                "notes": notes,
                "service_status_id": service_status_id,
                "evidence_description": evidence_description,
                "materials": materials,
            },
        )
