"""Refuse-safety net: every refusal is checked in both company directions and must
leave the whole database and upload folder exactly as they were.

A 500 anywhere fails these tests too: the TestClient re-raises server errors.
"""

from __future__ import annotations

import io
import re
import shutil
from collections import Counter
from pathlib import Path

import pytest
from sqlalchemy import func, select

from app import deps, fronts
from app.config import get_settings
from app.exceptions import BusinessError
from app.fronts import DEFAULT_FRONT, FRONTS_DIR
from app.models import (
    Company,
    Location,
    MaintenanceRecord,
    MaterialUsed,
    PhotoEvidence,
    ServiceRequest,
    TicketCounter,
)
from app.seed import DEMO_REQUESTS, placeholder_png
from app.services import maintenance as maintenance_service
from app.services import requests as request_service
from theme_helpers import effective_tokens

SAM = "sam.rivera@mail.example"
PNG = placeholder_png((90, 30, 30), 8, 6)


def world(db_session) -> tuple:
    """Everything a refused request could have changed."""
    db_session.expire_all()
    counts = tuple(
        db_session.scalar(select(func.count()).select_from(model))
        for model in (ServiceRequest, MaintenanceRecord, MaterialUsed, PhotoEvidence, TicketCounter)
    )
    tickets = tuple(
        db_session.execute(
            select(
                ServiceRequest.id,
                ServiceRequest.status_id,
                ServiceRequest.assigned_technician_id,
                ServiceRequest.assigned_at,
                ServiceRequest.updated_at,
            ).order_by(ServiceRequest.id)
        )
    )
    files = tuple(sorted(str(p) for p in Path(get_settings().upload_dir).rglob("*") if p.is_file()))
    return counts, tickets, files


# --- 1. company A technician cannot log work on a company B request id ----------

CROSS_LOG = [
    # (technician login, technician company, target ticket, target company)
    pytest.param("tess.tech@facility.example", "campus-ops", "REQ-2026-0001", "harbor-yard", id="campus-tech->harbor"),
    pytest.param("hana.tech@harbor-yard.example", "harbor-yard", "REQ-2026-0002", "campus-ops", id="harbor-tech->campus"),
]


@pytest.mark.parametrize(("login", "company", "ticket", "owner"), CROSS_LOG)
def test_technician_cannot_log_work_on_other_company_request(
    client, login_as, db_session, lookup, ui, login, company, ticket, owner
):
    login_as(login, company=company)
    target = lookup.request(ticket, owner)
    before = world(db_session)
    response = client.post(
        "/maintenance",
        data={
            "request_id": target.id,
            "work_performed": "cross-company attempt",
            "service_status_id": lookup.status_id("COMPLETED"),
            "material_name": ["Filter"],
            "material_quantity": ["1"],
        },
        files={"evidenceFile": ("p.png", io.BytesIO(PNG), "image/png")},
    )
    assert response.status_code == 200
    assert ui("error.requestNotFound") in response.text  # same as a non-existent id
    assert target.problem_description not in response.text  # nothing about B leaks
    assert world(db_session) == before  # no record, material, photo, file or status change


@pytest.mark.parametrize(("login", "company", "ticket", "owner"), CROSS_LOG)
def test_log_form_and_service_refuse_other_company_request(
    client, login_as, db_session, lookup, login, company, ticket, owner
):
    login_as(login, company=company)
    target = lookup.request(ticket, owner)
    page = client.get(f"/maintenance/new?requestId={target.id}").text
    assert target.problem_description not in page and 'class="full request-card"' not in page

    technician = lookup.user("T-2001" if company == "campus-ops" else "HY-T-5001")
    before = world(db_session)
    with pytest.raises(BusinessError) as info:
        maintenance_service.log_work(
            db_session, actor=technician, request_id=target.id, work_performed="x", notes=None,
            service_status_id=lookup.status_id("IN_PROGRESS"), materials=[],
        )
    assert info.value.message_key == "error.requestNotFound"
    assert world(db_session) == before


# --- 2. company A manager cannot assign on a company B request id ---------------


@pytest.mark.parametrize(
    ("login", "company", "ticket", "owner", "technician"),
    [
        pytest.param("morgan.manager@facility.example", "campus-ops", "REQ-2026-0002", "harbor-yard", "T-2001",
                     id="campus-mgr->harbor-ticket,own-tech"),
        pytest.param("morgan.manager@facility.example", "campus-ops", "REQ-2026-0002", "harbor-yard", "HY-T-5001",
                     id="campus-mgr->harbor-ticket,harbor-tech"),
        pytest.param(SAM, "harbor-yard", "REQ-2026-0001", "campus-ops", "HY-T-5001",
                     id="harbor-mgr->campus-ticket,own-tech"),
        pytest.param(SAM, "harbor-yard", "REQ-2026-0001", "campus-ops", "T-2001",
                     id="harbor-mgr->campus-ticket,campus-tech"),
    ],
)
def test_manager_cannot_assign_on_other_company_request(
    client, login_as, db_session, lookup, ui, login, company, ticket, owner, technician
):
    login_as(login, company=company)
    target = lookup.request(ticket, owner)
    assert target.status.code == "PENDING" and target.assigned_technician_id is None
    before = world(db_session)
    body = client.post(
        "/requests/assign",
        data={"requestId": target.id, "technicianId": lookup.user(technician).id},
        follow_redirects=True,
    ).text
    assert ui("error.requestNotFound") in body
    assert world(db_session) == before


def test_manager_cannot_assign_other_company_technician_on_own_request(client, login_as, db_session, lookup, ui):
    login_as("morgan.manager@facility.example")
    before = world(db_session)
    body = client.post(
        "/requests/assign",
        data={"requestId": lookup.request("REQ-2026-0001").id, "technicianId": lookup.user("HY-T-5001").id},
        follow_redirects=True,
    ).text
    assert ui("error.userNotFound") in body
    assert world(db_session) == before


# --- 3. company A manager /reports only counts company A rows -------------------


def _report_numbers(html: str) -> dict:
    values = [int(v) for v in re.findall(r'<div class="value">(\d+)</div>', html)]
    charts = {
        cid: [int(v) for v in re.search(rf'id="{cid}".*?data-values=\'([^\']*)\'', html, flags=re.S).group(1).strip("[]").split(",") if v.strip()]
        for cid in ("statusChart", "typeChart")
    }
    table = re.search(r"<h2>[^<]*</h2>\s*<div class=\"table-wrap\">.*?<tbody>(.*?)</tbody>", html, flags=re.S).group(1)
    return {
        "total": values[0], "pending": values[1], "in_progress": values[2], "completed": values[3],
        "status_chart": sum(charts["statusChart"]), "type_chart": sum(charts["typeChart"]),
        "rows": len(re.findall(r"<tr>\s*<td>REQ-", table)),  # data rows start with the ticket number
    }


def _expected(db_session, slug: str) -> dict:
    company_id = db_session.scalar(select(Company.id).where(Company.slug == slug))
    codes = Counter(
        r.status.code for r in db_session.scalars(select(ServiceRequest).where(ServiceRequest.company_id == company_id))
    )
    total = sum(codes.values())
    return {
        "total": total, "pending": codes["PENDING"], "in_progress": codes["IN_PROGRESS"],
        "completed": codes["COMPLETED"], "status_chart": total, "type_chart": total, "rows": total,
    }


@pytest.mark.parametrize(
    ("login", "company", "other"),
    [("morgan.manager@facility.example", "campus-ops", "harbor-yard"), (SAM, "harbor-yard", "campus-ops")],
)
def test_report_counts_only_own_company(client, login_as, db_session, lookup, login, company, other):
    # Make the other company busier so leaks would show in every number.
    outsider = lookup.user("HY-E-4001" if other == "harbor-yard" else "E-1001")
    location = "HY-WH-A" if other == "harbor-yard" else "OFC-A-1-ADM"
    for n in range(4):
        request_service.create_request(
            db_session, actor=outsider, location_id=lookup.location_id(location),
            problem_description=f"other company load {n}", priority_id=lookup.priority_id("LOW"),
            maintenance_type_id=lookup.type_id("IMPROVEMENT"),
        )

    login_as(login, company=company)
    html = client.get("/reports").text
    assert _report_numbers(html) == _expected(db_session, company)
    assert "other company load" not in html
    other_id = db_session.scalar(select(Company.id).where(Company.slug == other))
    for name in db_session.scalars(select(Location.name).where(Location.company_id == other_id)):
        assert name not in html  # neither in rows nor in the location filter


def test_report_with_other_company_location_filter_counts_zero(client, login_as, lookup):
    login_as("morgan.manager@facility.example")
    html = client.get("/reports", params={"locationId": lookup.location_id("HY-GATE-N")}).text
    numbers = _report_numbers(html)
    assert numbers["total"] == numbers["rows"] == numbers["status_chart"] == 0


def test_report_service_rows_are_all_own_company(db_session, lookup):
    from app.services import reports as report_service

    for emp in ("R-3001", "HY-R-6001"):
        actor = lookup.user(emp)
        rows = report_service.filtered_requests(db_session, actor=actor)
        assert rows and all(r.company_id == actor.company_id for r in rows)
        assert len(rows) == sum(1 for r in DEMO_REQUESTS if r[0] == actor.company.slug)


# --- 4. front fallback: remove an optional override, _default still renders -----


@pytest.fixture()
def fronts_copy(tmp_path, monkeypatch):
    """A copy of the real app/fronts tree that the app renders from (so we can delete files)."""
    root = tmp_path / "fronts"
    shutil.copytree(FRONTS_DIR, root, ignore=shutil.ignore_patterns("__pycache__", "*.py"))
    monkeypatch.setattr(deps, "templates_for", lambda slug: fronts.templates_for(slug, root=root))
    return root


PAGES = ["/", "/requests", "/requests/new", "/reports"]


def test_real_override_is_used_while_present(client, login_as, fronts_copy):
    login_as(SAM, company="harbor-yard")
    html = client.get("/").text
    assert 'data-front="harbor-yard"' in html and "brand-harbor" in html


@pytest.mark.parametrize(
    ("removed", "brand_front", "theme_front", "accent"),
    [
        pytest.param(["harbor-yard/templates/partials/brand.html"], DEFAULT_FRONT, "harbor-yard", "#1f5f8b",
                     id="brand-partial-removed"),
        pytest.param(["harbor-yard/templates/partials/theme.css"], "harbor-yard", DEFAULT_FRONT, "#c45c26",
                     id="theme-css-removed"),
        pytest.param(["harbor-yard"], DEFAULT_FRONT, DEFAULT_FRONT, "#c45c26", id="whole-front-removed"),
    ],
)
def test_removed_override_falls_back_to_default(
    client, login_as, ui, fronts_copy, removed, brand_front, theme_front, accent
):
    for rel in removed:
        target = fronts_copy / rel
        shutil.rmtree(target) if target.is_dir() else target.unlink()
    login_as(SAM, company="harbor-yard")
    for path in PAGES:
        response = client.get(path)
        assert response.status_code == 200, path
        assert f'data-front="{brand_front}"' in response.text, path  # brand partial that rendered
        assert f'data-theme="{theme_front}"' in response.text, path  # theme hook that rendered
        assert 'data-company="harbor-yard"' in response.text, path  # still the same company
        assert effective_tokens(response.text)["accent"] == accent, path
    if brand_front == DEFAULT_FRONT:
        assert ui("brand.title") in client.get("/").text  # the _default brand text


def test_company_without_front_folder_renders_default(client, login_as, fronts_copy):
    assert not (fronts_copy / "campus-ops").exists()
    login_as("morgan.manager@facility.example")
    for path in PAGES:
        response = client.get(path)
        assert response.status_code == 200 and 'data-front="_default"' in response.text, path


# --- 5. still-missing refusals --------------------------------------------------

# Open redirect ------------------------------------------------------------------

OFFSITE = ["//evil.example/", "https://evil.example/", "/\\evil.example", "/\t/evil.example", "javascript:alert(1)"]


@pytest.mark.parametrize("target", OFFSITE)
def test_open_redirect_refused_everywhere(client, login_as, target):
    first = client.post(
        "/login",
        data={"company": "campus-ops", "login": "R-3001", "password": "OfficeDemo1!", "next": target},
        follow_redirects=False,
    )
    assert first.headers["location"] == "/"
    assert client.get("/login", params={"next": target}, follow_redirects=False).headers["location"] == "/"
    assert client.get("/lang/es", params={"next": target}, follow_redirects=False).headers["location"] == "/"
    # A rejected form never bounces to a foreign Referer either.
    rejected = client.post("/requests", data={}, csrf=False, headers={"referer": target}, follow_redirects=False)
    assert rejected.headers["location"] == "/"


# CSRF missing token --------------------------------------------------------------


@pytest.mark.parametrize(
    ("login", "path", "data"),
    [
        (None, "/login", {"company": "campus-ops", "login": "E-1001", "password": "OfficeDemo1!"}),
        ("ada.employee@facility.example", "/logout", {}),
        ("ada.employee@facility.example", "/requests", {"location_id": "1", "priority_id": "1",
                                                          "maintenance_type_id": "1", "problem_description": "x"}),
        ("morgan.manager@facility.example", "/requests/assign", {"requestId": "1", "technicianId": "4"}),
        ("tess.tech@facility.example", "/maintenance", {"request_id": "2", "work_performed": "x",
                                                        "service_status_id": "3"}),
    ],
)
def test_missing_csrf_token_changes_nothing(client, login_as, db_session, session_data, login, path, data):
    if login:
        login_as(login)
    signed_in = "user_id" in session_data()
    before = world(db_session)
    response = client.post(path, data=data, csrf=False, follow_redirects=False)
    assert response.status_code == 303
    assert world(db_session) == before
    assert ("user_id" in session_data()) is signed_in  # neither logged in nor logged out


# Unauthenticated ------------------------------------------------------------------


def test_unauthenticated_evidence_refused(client, db_session):
    photo = db_session.scalar(select(PhotoEvidence).order_by(PhotoEvidence.id))
    response = client.get(f"/evidence/{photo.id}", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login?next=")
    assert not response.headers.get("content-type", "").startswith("image/")
    assert b"\x89PNG" not in response.content
    assert client.get(f"/uploads/{photo.relative_path}").status_code == 404


@pytest.mark.parametrize("path", ["/", "/requests", "/requests/new", "/requests/1", "/maintenance/new", "/reports", "/evidence/1"])
def test_unauthenticated_pages_redirect_to_login(client, path):
    response = client.get(path, follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/login")


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/requests", {"location_id": "1", "priority_id": "1", "maintenance_type_id": "1", "problem_description": "anon"}),
        ("/requests/assign", {"requestId": "1", "technicianId": "4"}),
        ("/maintenance", {"request_id": "2", "work_performed": "anon", "service_status_id": "3"}),
    ],
)
def test_unauthenticated_writes_refused_even_with_valid_csrf(client, db_session, path, data):
    """An anonymous session has a valid CSRF token; the write must still need a user."""
    before = world(db_session)
    response = client.post(path, data=data, follow_redirects=False)  # token auto-included
    assert response.status_code == 303 and response.headers["location"].startswith("/login")
    assert world(db_session) == before


# Bad report input -----------------------------------------------------------------


@pytest.mark.parametrize(
    "params",
    [
        {"fromDate": "not-a-date"},
        {"toDate": "2026-02-30"},
        {"fromDate": "2026-10-22T00:00"},
        {"fromDate": "99999-01-01"},
        {"fromDate": "\u0662\u0660\u0662\u0666-01-01"},  # non-ASCII digits
        {"fromDate": "2026-10-22", "toDate": "%00"},
    ],
)
def test_bad_report_date_does_not_500(client, login_as, ui, params):
    login_as("morgan.manager@facility.example")
    response = client.get("/reports", params=params)
    assert response.status_code == 200
    assert ui("error.invalidDate") in response.text


@pytest.mark.parametrize("params", [{"locationId": "abc"}, {"maintenanceTypeId": "x"}, {"locationId": "1e9999"}])
def test_bad_report_ids_are_refused_without_500(client, login_as, params):
    login_as("morgan.manager@facility.example")
    assert 400 <= client.get("/reports", params=params).status_code < 500


# Bogus catalog ids on create ---------------------------------------------------------


@pytest.mark.parametrize(
    ("field", "value", "key"),
    [
        ("location_id", "999999", "error.locationInvalid"),
        ("location_id", "-1", "error.locationInvalid"),
        ("location_id", "1; DROP TABLE location", "error.locationInvalid"),
        ("location_id", "other-company", "error.locationInvalid"),
        ("priority_id", "999999", "error.priorityInvalid"),
        ("priority_id", "0", "error.priorityInvalid"),
        ("maintenance_type_id", "999999", "error.typeInvalid"),
        ("maintenance_type_id", "", "error.typeInvalid"),
    ],
)
def test_create_with_bogus_catalog_id_does_not_500(client, login_as, db_session, lookup, ui, field, value, key):
    login_as("ada.employee@facility.example")
    if value == "other-company":
        value = str(lookup.location_id("HY-GATE-N"))
    data = {
        "location_id": str(lookup.location_id("OFC-A-1-ADM")),
        "priority_id": str(lookup.priority_id("LOW")),
        "maintenance_type_id": str(lookup.type_id("CORRECTIVE")),
        "problem_description": "bogus catalog probe",
        field: value,
    }
    before = world(db_session)
    response = client.post("/requests", data=data)
    assert response.status_code == 200
    assert ui(key) in response.text
    assert world(db_session) == before
