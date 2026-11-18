from __future__ import annotations

from typing import Optional

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.exceptions import BusinessError
from app.models import AppUser, MaintenanceRecord, MaterialUsed, PhotoEvidence
from app.services import catalog, evidence, requests as request_service


def log_work(
    db: Session,
    *,
    actor: AppUser,
    request_id: int,
    work_performed: str,
    notes: Optional[str],
    service_status_id: int,
    materials: list[dict],
    evidence_file: Optional[UploadFile] = None,
    evidence_description: Optional[str] = None,
) -> int:
    if not actor.is_technician():
        raise BusinessError("Only technicians can log maintenance work.", "error.onlyTechniciansLog")

    req = request_service.require_by_id(db, request_id)
    if req.assigned_technician_id is None or req.assigned_technician_id != actor.id:
        raise BusinessError(
            "You can only log work on requests assigned to you.",
            "error.notYourRequest",
        )
    if req.status.code == "CANCELLED":
        raise BusinessError("Cannot log work on a cancelled request.", "error.cancelledRequest")

    status_code = catalog.status_code_by_id(db, service_status_id)
    if status_code == "PENDING":
        raise BusinessError(
            "Service status after attendance cannot be PENDING.",
            "error.statusNotPending",
        )

    work = (work_performed or "").strip()
    if not work:
        raise BusinessError("Work performed is required.", "error.workRequired")

    record = MaintenanceRecord(
        request_id=request_id,
        technician_id=actor.id,
        work_performed=work,
        notes=(notes or "").strip() or None,
        service_status_id=service_status_id,
    )
    db.add(record)
    db.flush()

    for line in materials or []:
        name = (line.get("name") or "").strip()
        if not name:
            continue
        qty = line.get("quantity")
        try:
            quantity = float(qty) if qty not in (None, "") else 1.0
        except (TypeError, ValueError):
            quantity = 1.0
        if quantity <= 0:
            quantity = 1.0
        unit = (line.get("unit") or "ea").strip() or "ea"
        mnotes = (line.get("notes") or "").strip() or None
        db.add(
            MaterialUsed(
                record_id=record.id,
                name=name,
                quantity=quantity,
                unit=unit,
                notes=mnotes,
            )
        )

    stored = evidence.store_evidence(request_id, evidence_file)
    if stored is not None:
        db.add(
            PhotoEvidence(
                record_id=record.id,
                file_name=stored.file_name,
                path_or_url=stored.public_path,
                description=(evidence_description or "").strip() or None,
            )
        )

    # Note C: keep request current status in sync inside the same transaction
    request_service.update_status(db, request_id, service_status_id)
    db.commit()
    return record.id
