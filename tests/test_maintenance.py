"""Logging work: status rules, closed tickets, material validation, photo failures."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app.config import get_settings
from app.exceptions import BusinessError
from app.models import MaintenanceRecord, MaterialUsed, PhotoEvidence, ServiceRequest
from app.seed import placeholder_png
from app.services import catalog, evidence
from app.services import maintenance as maintenance_service

PNG = placeholder_png((10, 120, 90), 8, 6)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 32
GIF = b"GIF89a" + b"\x00" * 32
WEBP = b"RIFF\x24\x00\x00\x00WEBPVP8 " + b"\x00" * 32


def _records(db_session, request_id: int) -> list[MaintenanceRecord]:
    db_session.expire_all()
    return list(
        db_session.scalars(
            select(MaintenanceRecord)
            .where(MaintenanceRecord.request_id == request_id)
            .order_by(MaintenanceRecord.id)
        )
    )


def _files() -> list[Path]:
    return sorted(p for p in Path(get_settings().upload_dir).rglob("*") if p.is_file())


def _snapshot(db_session, request_id: int):
    db_session.expire_all()
    return (
        len(_records(db_session, request_id)),
        db_session.scalar(select(func.count()).select_from(MaterialUsed)),
        db_session.scalar(select(func.count()).select_from(PhotoEvidence)),
        db_session.get(ServiceRequest, request_id).status.code,
        _files(),
    )


def _log(client, request_id: int, status_id: int, *, files=None, **extra):
    data = {
        "request_id": request_id,
        "work_performed": "Finished refrigerant recharge and verified temperature.",
        "notes": "Stable at 20C",
        "service_status_id": status_id,
        "evidence_description": "",
    }
    data.update(extra)
    return client.post("/maintenance", data=data, files=files, follow_redirects=False)


@pytest.fixture()
def tess(login_as, lookup):
    login_as("tess.tech@facility.example")
    return lookup.request("REQ-2026-0002")  # IN_PROGRESS, assigned to Tess


# --- happy path -----------------------------------------------------------------


def test_technician_logs_work(client, db_session, lookup, tess, ui):
    response = _log(
        client,
        tess.id,
        lookup.status_id("COMPLETED"),
        material_name=["R-410A refrigerant", "", "Cable ties"],
        material_quantity=["2", "", "2.5"],
        material_unit=["kg", "ea", ""],
        material_notes=["top-up", "", ""],
    )
    assert response.status_code == 303
    assert response.headers["location"] == "/requests"

    latest = _records(db_session, tess.id)[-1]
    assert latest.technician.employee_number == "T-2001"
    assert latest.service_status.code == "COMPLETED"
    materials = {m.name: (m.quantity, m.unit, m.notes) for m in latest.materials}
    assert materials == {"R-410A refrigerant": (2.0, "kg", "top-up"), "Cable ties": (2.5, "ea", None)}
    assert db_session.get(ServiceRequest, tess.id).status.code == "COMPLETED"
    assert ui("maintenance.saved") in client.get("/requests").text


@pytest.mark.parametrize("status", ["IN_PROGRESS", "COMPLETED", "CANCELLED"])
def test_every_loggable_status_is_accepted(client, db_session, lookup, tess, status):
    assert _log(client, tess.id, lookup.status_id(status)).status_code == 303
    assert db_session.get(ServiceRequest, tess.id).status.code == status


@pytest.mark.parametrize(("name", "content_type", "data"), [
    ("p.png", "image/png", PNG), ("p.jpg", "image/jpeg", JPEG), ("p.gif", "image/gif", GIF), ("p.webp", "image/webp", WEBP),
])
def test_allowed_photo_types_are_stored(client, db_session, lookup, tess, name, content_type, data):
    response = _log(
        client, tess.id, lookup.status_id("IN_PROGRESS"),
        evidence_description="Proof", files={"evidenceFile": (name, io.BytesIO(data), content_type)},
    )
    assert response.status_code == 303
    photo = _records(db_session, tess.id)[-1].photos[0]
    assert photo.description == "Proof"
    assert photo.relative_path.endswith(evidence.ALLOWED_CONTENT_TYPES[content_type])
    assert (Path(get_settings().upload_dir) / photo.relative_path).read_bytes() == data


# --- status rules -----------------------------------------------------------------


def test_only_loggable_statuses(db_session):
    assert [s.code for s in catalog.loggable_statuses(db_session)] == ["IN_PROGRESS", "COMPLETED", "CANCELLED"]


def test_pending_is_not_a_loggable_status(client, db_session, lookup, tess, ui):
    before = _snapshot(db_session, tess.id)
    response = _log(client, tess.id, lookup.status_id("PENDING"))
    assert response.status_code == 200
    assert ui("error.statusNotAllowed") in response.text
    assert _snapshot(db_session, tess.id) == before


def test_unknown_status_id(client, db_session, tess, ui):
    before = _snapshot(db_session, tess.id)
    assert ui("error.invalidStatus") in _log(client, tess.id, 999_999).text
    assert _snapshot(db_session, tess.id) == before


@pytest.mark.parametrize(("login", "ticket"), [
    ("theo.tech@facility.example", "REQ-2026-0003"),  # seeded COMPLETED
    ("hana.tech@harbor-yard.example", "REQ-2026-0003"),  # harbor, seeded COMPLETED
])
def test_completed_request_refuses_more_work(client, login_as, db_session, lookup, ui, login, ticket):
    company = "harbor-yard" if "harbor" in login else "campus-ops"
    login_as(login, company=company)
    req = lookup.request(ticket, company)
    before = _snapshot(db_session, req.id)
    response = _log(client, req.id, lookup.status_id("IN_PROGRESS"))
    assert response.status_code == 200
    assert ui("error.requestClosed") in response.text
    assert _snapshot(db_session, req.id) == before  # not reopened, nothing written


def test_cancelled_request_refuses_more_work(client, login_as, db_session, lookup, ui):
    req = lookup.request("REQ-2026-0002")
    req.status_id = lookup.status_id("CANCELLED")
    db_session.commit()
    login_as("tess.tech@facility.example")
    before = _snapshot(db_session, req.id)
    assert ui("error.requestClosed") in _log(client, req.id, lookup.status_id("IN_PROGRESS")).text
    assert _snapshot(db_session, req.id) == before


def test_closing_then_logging_again_is_refused(client, db_session, lookup, tess, ui):
    assert _log(client, tess.id, lookup.status_id("COMPLETED")).status_code == 303
    assert ui("error.requestClosed") in _log(client, tess.id, lookup.status_id("IN_PROGRESS")).text


def test_closed_tickets_are_not_offered_for_logging(client, login_as, lookup):
    login_as("theo.tech@facility.example")  # REQ-0003 completed, REQ-0004 in progress
    closed, open_ = lookup.request("REQ-2026-0003"), lookup.request("REQ-2026-0004")
    page = client.get("/maintenance/new").text
    ticket_select = re.search(r'<select name="request_id".*?</select>', page, flags=re.S).group(0)
    assert f'<option value="{open_.id}"' in ticket_select
    assert f'<option value="{closed.id}"' not in ticket_select
    assert 'class="full request-card"' not in client.get(f"/maintenance/new?requestId={closed.id}").text
    listing = client.get("/requests").text
    assert f"/maintenance/new?requestId={open_.id}" in listing
    assert f"/maintenance/new?requestId={closed.id}" not in listing


# --- materials --------------------------------------------------------------------


@pytest.mark.parametrize("quantity", ["-3", "0", "abc", "", "nan", "inf", "1e999", "2,5"])
def test_invalid_material_quantity_is_rejected(client, db_session, lookup, tess, ui, quantity):
    before = _snapshot(db_session, tess.id)
    response = _log(
        client, tess.id, lookup.status_id("COMPLETED"),
        material_name=["Filter"], material_quantity=[quantity], material_unit=["ea"], material_notes=[""],
    )
    assert response.status_code == 200
    assert ui("error.materialQuantity", name="Filter") in response.text
    assert _snapshot(db_session, tess.id) == before  # no record, no materials, status unchanged


def test_quantity_without_material_name_is_rejected(client, db_session, lookup, tess, ui):
    before = _snapshot(db_session, tess.id)
    response = _log(client, tess.id, lookup.status_id("IN_PROGRESS"), material_name=[""], material_quantity=["3"])
    assert ui("error.materialName") in response.text
    assert _snapshot(db_session, tess.id) == before


def test_material_error_keeps_the_form_and_is_translated(client, set_lang, lookup, tess, ui):
    set_lang("es")
    response = _log(
        client, tess.id, lookup.status_id("IN_PROGRESS"),
        work_performed="Keep me", material_name=["Grasa"], material_quantity=["-1"],
    )
    assert ui("error.materialQuantity", "es", name="Grasa") in response.text
    assert "Keep me" in response.text and 'value="Grasa"' in response.text


def test_parse_materials_unit():
    lines = maintenance_service.parse_materials(
        [{"name": " Tape ", "quantity": "1.5", "unit": "", "notes": " "}, {"name": "", "quantity": "", "unit": "ea"}]
    )
    assert [(m.name, m.quantity, m.unit, m.notes) for m in lines] == [("Tape", 1.5, "ea", None)]
    with pytest.raises(BusinessError) as info:
        maintenance_service.parse_materials([{"name": "Tape", "quantity": "-1"}])
    assert info.value.message_key == "error.materialQuantity" and info.value.params == {"name": "Tape"}


# --- photo failures: surfaced, nothing saved ----------------------------------------


@pytest.mark.parametrize(("upload", "key"), [
    (("notes.txt", b"hello", "text/plain"), "error.evidenceType"),
    (("x.svg", b"<svg onload=alert(1)>", "image/svg+xml"), "error.evidenceType"),
    (("fake.png", b"not really a png", "image/png"), "error.evidenceType"),  # content must match type
    (("fake.jpg", PNG, "image/jpeg"), "error.evidenceType"),
    (("empty.png", b"", "image/png"), "error.evidenceEmpty"),
])
def test_bad_photo_is_reported_and_nothing_is_saved(client, db_session, lookup, tess, ui, upload, key):
    name, data, content_type = upload
    before = _snapshot(db_session, tess.id)
    response = _log(
        client, tess.id, lookup.status_id("COMPLETED"),
        material_name=["Filter"], material_quantity=["1"],
        files={"evidenceFile": (name, io.BytesIO(data), content_type)},
    )
    assert response.status_code == 200
    assert ui(key) in response.text
    assert _snapshot(db_session, tess.id) == before  # no record pretending the photo landed


def test_oversized_photo_is_reported(client, db_session, lookup, tess, ui, monkeypatch):
    monkeypatch.setattr(evidence, "MAX_BYTES", 10)
    before = _snapshot(db_session, tess.id)
    response = _log(client, tess.id, lookup.status_id("IN_PROGRESS"), files={"evidenceFile": ("big.png", io.BytesIO(PNG), "image/png")})
    assert ui("error.evidenceTooLarge") in response.text
    assert _snapshot(db_session, tess.id) == before


def test_disk_failure_saves_nothing(client, db_session, lookup, tess, ui, monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(evidence, "write_evidence", broken)
    before = _snapshot(db_session, tess.id)
    response = _log(client, tess.id, lookup.status_id("IN_PROGRESS"), files={"evidenceFile": ("p.png", io.BytesIO(PNG), "image/png")})
    assert ui("error.evidenceSaveFailed") in response.text
    assert _snapshot(db_session, tess.id) == before


def test_failed_commit_removes_the_written_file(db_session, lookup, monkeypatch):
    """If the database step fails after the photo was written, the file is deleted again."""
    from fastapi import UploadFile

    from app.services import requests as request_service

    def explode(*_args, **_kwargs):
        raise RuntimeError("db down")

    monkeypatch.setattr(request_service, "update_status", explode)
    req = lookup.request("REQ-2026-0002")
    before = _snapshot(db_session, req.id)
    upload = UploadFile(io.BytesIO(PNG), filename="p.png", headers={"content-type": "image/png"})
    with pytest.raises(RuntimeError):
        maintenance_service.log_work(
            db_session, actor=lookup.user("T-2001"), request_id=req.id, work_performed="x", notes=None,
            service_status_id=lookup.status_id("IN_PROGRESS"), materials=[], evidence_file=upload,
        )
    assert _snapshot(db_session, req.id) == before


def test_no_photo_chosen_is_fine(client, db_session, lookup, tess):
    response = _log(client, tess.id, lookup.status_id("IN_PROGRESS"), files={"evidenceFile": ("", io.BytesIO(b""), "application/octet-stream")})
    assert response.status_code == 303
    assert _records(db_session, tess.id)[-1].photos == []


def test_prepare_evidence_without_file_returns_none():
    assert evidence.prepare_evidence(None) is None


# --- existing access rules --------------------------------------------------------


@pytest.mark.parametrize(
    ("ticket", "login", "extra", "key"),
    [
        ("REQ-2026-0004", "tess.tech@facility.example", {}, "error.notYourRequest"),
        ("REQ-2026-0002", "tess.tech@facility.example", {"work_performed": "   "}, "error.workRequired"),
        ("REQ-2026-0002", "morgan.manager@facility.example", {}, "error.onlyTechniciansLog"),
    ],
)
def test_log_work_access_rules(client, login_as, db_session, lookup, ui, ticket, login, extra, key):
    login_as(login)
    req = lookup.request(ticket)
    before = _snapshot(db_session, req.id)
    response = _log(client, req.id, lookup.status_id("IN_PROGRESS"), **extra)
    assert response.status_code == 200
    assert ui(key) in response.text
    assert _snapshot(db_session, req.id) == before


def test_unknown_request(client, lookup, tess, ui):
    assert ui("error.requestNotFound") in _log(client, 999_999, lookup.status_id("IN_PROGRESS")).text


def test_form_preselects_only_own_open_ticket(client, lookup, tess):
    page = client.get(f"/maintenance/new?requestId={tess.id}").text
    assert 'class="full request-card"' in page and tess.problem_description in page
    other = lookup.request("REQ-2026-0004")
    assert 'class="full request-card"' not in client.get(f"/maintenance/new?requestId={other.id}").text
    missing = client.get("/maintenance/new?requestId=999999")
    assert missing.status_code == 200 and 'class="full request-card"' not in missing.text


def test_employee_forbidden_from_log_form(client, login_as, ui):
    login_as("ada.employee@facility.example")
    response = client.get("/maintenance/new", follow_redirects=False)
    assert response.status_code == 303
    assert ui("forbidden") in client.get("/").text


def test_missing_status_code_raises_translatable_error(db_session):
    with pytest.raises(BusinessError) as info:
        catalog.status_id_by_code(db_session, "ON_HOLD")
    assert info.value.message_key == "error.statusMissing"


def test_technician_only_service_guard(db_session, lookup):
    from app.services import requests as request_service

    with pytest.raises(BusinessError) as info:
        request_service.list_assignable_for_technician(db_session, lookup.user("E-1001"))
    assert info.value.message_key == "error.onlyTechniciansForm"


# --- service-level rules (log_work called directly) ---------------------------------


def _service_log(db_session, lookup, *, emp="T-2001", ticket="REQ-2026-0002", status="IN_PROGRESS", status_id=None,
                 materials=None, upload=None):
    return maintenance_service.log_work(
        db_session,
        actor=lookup.user(emp),
        request_id=lookup.request(ticket).id,
        work_performed="service-level check",
        notes=None,
        service_status_id=status_id or lookup.status_id(status),
        materials=materials or [],
        evidence_file=upload,
    )


def _upload(data: bytes, content_type: str, name: str = "p.png"):
    from fastapi import UploadFile

    return UploadFile(io.BytesIO(data), filename=name, headers={"content-type": content_type})


@pytest.mark.parametrize("quantity", ["0", "-1", "-0.5", "abc", "", "nan", "inf"])
def test_service_rejects_bad_quantity_instead_of_coercing_to_one(db_session, lookup, quantity):
    req = lookup.request("REQ-2026-0002")
    before = _snapshot(db_session, req.id)
    with pytest.raises(BusinessError) as info:
        _service_log(db_session, lookup, materials=[{"name": "Filter", "quantity": quantity, "unit": "ea"}])
    assert info.value.message_key == "error.materialQuantity" and info.value.params == {"name": "Filter"}
    assert _snapshot(db_session, req.id) == before
    assert db_session.scalar(select(func.count()).select_from(MaterialUsed).where(MaterialUsed.name == "Filter")) == 0


@pytest.mark.parametrize(("ticket", "emp", "closed_status"), [
    ("REQ-2026-0003", "T-2002", "COMPLETED"),  # seeded completed
    ("REQ-2026-0002", "T-2001", "CANCELLED"),
])
def test_service_refuses_logging_on_closed_request(db_session, lookup, ticket, emp, closed_status):
    req = lookup.request(ticket)
    req.status_id = lookup.status_id(closed_status)
    db_session.commit()
    before = _snapshot(db_session, req.id)
    for target in ("IN_PROGRESS", "COMPLETED", "CANCELLED"):
        with pytest.raises(BusinessError) as info:
            _service_log(db_session, lookup, emp=emp, ticket=ticket, status=target)
        assert info.value.message_key == "error.requestClosed"
    assert _snapshot(db_session, req.id) == before


def test_service_target_status_must_be_in_loggable_statuses(db_session, lookup):
    """Not just 'not PENDING': any status outside loggable_statuses() is refused."""
    from app.models import RequestStatus

    on_hold = RequestStatus(code="ON_HOLD", name="On hold", is_final=False)
    db_session.add(on_hold)
    db_session.commit()
    assert on_hold.id not in {s.id for s in catalog.loggable_statuses(db_session)}
    req = lookup.request("REQ-2026-0002")
    before = _snapshot(db_session, req.id)
    for status_id in (lookup.status_id("PENDING"), on_hold.id):
        with pytest.raises(BusinessError) as info:
            _service_log(db_session, lookup, status_id=status_id)
        assert info.value.message_key == "error.statusNotAllowed"
    assert _snapshot(db_session, req.id) == before


@pytest.mark.parametrize(("data", "content_type", "key"), [
    (b"plain text", "text/plain", "error.evidenceType"),
    (b"<svg/>", "image/svg+xml", "error.evidenceType"),
    (b"GIF89a but declared png", "image/png", "error.evidenceType"),
    (PNG, "application/pdf", "error.evidenceType"),
    ("oversize", "image/png", "error.evidenceTooLarge"),
])
def test_service_evidence_failure_saves_no_record_and_no_photo_row(db_session, lookup, monkeypatch, data, content_type, key):
    if data == "oversize":
        monkeypatch.setattr(evidence, "MAX_BYTES", 16)
        data = PNG  # a real PNG, just larger than the (patched) limit
    req = lookup.request("REQ-2026-0002")
    before = _snapshot(db_session, req.id)
    with pytest.raises(BusinessError) as info:
        _service_log(db_session, lookup, materials=[{"name": "Tape", "quantity": "1"}], upload=_upload(data, content_type))
    assert info.value.message_key == key
    assert _snapshot(db_session, req.id) == before  # no record, no material, no photo row, no file, same status
