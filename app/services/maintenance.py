"""Logging maintenance work: validate everything first, then write record + photo together."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Optional

from fastapi import UploadFile
from sqlalchemy.orm import Session

from app.exceptions import BusinessError
from app.models import AppUser, MaintenanceRecord, MaterialUsed, PhotoEvidence
from app.services import catalog, evidence, requests as request_service

logger = logging.getLogger(__name__)


@dataclass
class MaterialLine:
    name: str
    quantity: float
    unit: str
    notes: Optional[str]


def parse_materials(lines: list[dict]) -> list[MaterialLine]:
    """Validate material rows. Blank rows (no name, no quantity) are skipped.

    A row with a name needs a finite quantity > 0; a row with a quantity needs a
    name. Anything else is a BusinessError (no silent coercion to 1.0).
    """
    parsed: list[MaterialLine] = []
    for line in lines or []:
        name = (line.get("name") or "").strip()
        raw_qty = str(line.get("quantity") or "").strip()
        if not name and not raw_qty:
            continue  # empty row (the form prefills unit "ea")
        if not name:
            raise BusinessError("Material name is required when a quantity is given.", "error.materialName")
        try:
            quantity = float(raw_qty)
        except ValueError:
            quantity = math.nan
        if not math.isfinite(quantity) or quantity <= 0:
            raise BusinessError(
                f"Material {name}: quantity must be a number greater than 0.",
                "error.materialQuantity",
                name=name,
            )
        parsed.append(
            MaterialLine(
                name=name,
                quantity=quantity,
                unit=(line.get("unit") or "ea").strip() or "ea",
                notes=(line.get("notes") or "").strip() or None,
            )
        )
    return parsed


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
    # --- 1. validate everything; nothing is written yet -------------------------
    if not actor.is_technician():
        raise BusinessError("Only technicians can log maintenance work.", "error.onlyTechniciansLog")

    req = request_service.require_by_id(db, request_id, company_id=actor.company_id)
    if req.assigned_technician_id is None or req.assigned_technician_id != actor.id:
        raise BusinessError(
            "You can only log work on requests assigned to you.",
            "error.notYourRequest",
        )
    if req.status.is_final:  # COMPLETED or CANCELLED
        raise BusinessError("This request is already closed.", "error.requestClosed")

    catalog.status_code_by_id(db, service_status_id)  # unknown id -> error.invalidStatus
    if service_status_id not in {s.id for s in catalog.loggable_statuses(db)}:
        raise BusinessError(
            "Service status must be In progress, Completed, or Cancelled.",
            "error.statusNotAllowed",
        )

    work = (work_performed or "").strip()
    if not work:
        raise BusinessError("Work performed is required.", "error.workRequired")

    material_lines = parse_materials(materials)
    photo = evidence.prepare_evidence(evidence_file)  # type/size/content checked here

    # --- 2. write record, materials, photo and status in one transaction --------
    stored = None
    try:
        record = MaintenanceRecord(
            request_id=request_id,
            technician_id=actor.id,
            work_performed=work,
            notes=(notes or "").strip() or None,
            service_status_id=service_status_id,
        )
        db.add(record)
        db.flush()
        for line in material_lines:
            db.add(
                MaterialUsed(
                    record_id=record.id,
                    name=line.name,
                    quantity=line.quantity,
                    unit=line.unit,
                    notes=line.notes,
                )
            )
        if photo is not None:
            stored = evidence.write_evidence(req.company_id, request_id, photo)
            db.add(
                PhotoEvidence(
                    record_id=record.id,
                    file_name=stored.file_name,
                    relative_path=stored.relative_path,
                    description=(evidence_description or "").strip() or None,
                )
            )
        # Keep the request's current status in sync inside the same transaction.
        request_service.update_status(db, request_id, service_status_id, company_id=actor.company_id)
        db.commit()
    except Exception as exc:
        db.rollback()
        if stored is not None:
            stored.absolute_path.unlink(missing_ok=True)  # no orphan file for a record that was not saved
        if isinstance(exc, OSError):
            logger.exception("Could not store evidence for request %s", request_id)
            raise BusinessError("The photo could not be stored.", "error.evidenceSaveFailed") from None
        raise
    return record.id
