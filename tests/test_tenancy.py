"""Company isolation for locations, service requests, work logs, reports, and the header."""

from __future__ import annotations

import re

import pytest
from sqlalchemy import select

from app.exceptions import BusinessError
from app.models import Location, MaintenanceRecord, ServiceRequest
from app.seed import DEMO_LOCATIONS, DEMO_REQUESTS
from app.services import catalog, requests as request_service

CAMPUS_DESCRIPTIONS = [r[4] for r in DEMO_REQUESTS if r[0] == "campus-ops"]
HARBOR_DESCRIPTIONS = [r[4] for r in DEMO_REQUESTS if r[0] == "harbor-yard"]
SAM = "sam.rivera@mail.example"


def _visible(body: str, descriptions: list[str]) -> list[str]:
    return [d for d in descriptions if d in body]


# --- seed integrity -----------------------------------------------------------


def test_each_company_has_its_own_locations(db_session, lookup):
    for slug, rows in DEMO_LOCATIONS.items():
        company_id = lookup.company(slug).id
        codes = set(db_session.scalars(select(Location.code).where(Location.company_id == company_id)))
        assert codes == {r[0] for r in rows}


def test_every_request_is_consistent_with_its_company(db_session):
    """Location, requester, technician, and work records all belong to the request's company."""
    requests = list(db_session.scalars(select(ServiceRequest)))
    assert {r.company.slug for r in requests} == {"campus-ops", "harbor-yard"}
    for req in requests:
        assert req.location.company_id == req.company_id
        assert req.requester.company_id == req.company_id
        if req.assigned_technician:
            assert req.assigned_technician.company_id == req.company_id
        for record in req.records:
            assert record.technician.company_id == req.company_id


def test_ticket_numbers_repeat_across_companies_but_not_within(db_session, lookup):
    campus = lookup.request("REQ-2026-0001", "campus-ops")
    harbor = lookup.request("REQ-2026-0001", "harbor-yard")
    assert campus.id != harbor.id
    assert campus.problem_description != harbor.problem_description


def test_maintenance_records_have_no_company_column():
    """Records inherit the company through their request."""
    assert "company_id" not in MaintenanceRecord.__table__.columns


# --- lists --------------------------------------------------------------------


def test_campus_manager_list_shows_only_campus(client, login_as):
    login_as("morgan.manager@facility.example")
    body = client.get("/requests").text
    assert _visible(body, CAMPUS_DESCRIPTIONS) == CAMPUS_DESCRIPTIONS
    assert _visible(body, HARBOR_DESCRIPTIONS) == []


def test_harbor_manager_list_shows_only_harbor(client, login_as):
    login_as(SAM, company="harbor-yard")
    body = client.get("/requests").text
    assert _visible(body, HARBOR_DESCRIPTIONS) == HARBOR_DESCRIPTIONS
    assert _visible(body, CAMPUS_DESCRIPTIONS) == []
    assert "Hana Technician" in body and "Tess Technician" not in body


def test_search_does_not_cross_companies(client, login_as):
    login_as("morgan.manager@facility.example")
    assert _visible(client.get("/requests", params={"q": "barrier"}).text, HARBOR_DESCRIPTIONS) == []


def test_harbor_technician_sees_assigned_harbor_work(client, login_as):
    login_as("hana.tech@harbor-yard.example")
    body = client.get("/requests").text
    assert "North gate barrier arm" in body
    assert _visible(body, CAMPUS_DESCRIPTIONS) == []


# --- new-request form ---------------------------------------------------------


def test_new_request_form_lists_only_own_locations(client, login_as):
    login_as("hugo.employee@harbor-yard.example")
    body = client.get("/requests/new").text
    for code, *_ in DEMO_LOCATIONS["harbor-yard"]:
        assert code in body
    for code, *_ in DEMO_LOCATIONS["campus-ops"]:
        assert code not in body


def test_catalog_locations_are_company_scoped(db_session, lookup):
    harbor = lookup.company("harbor-yard").id
    assert {loc.code for loc in catalog.list_locations(db_session, company_id=harbor)} == {
        r[0] for r in DEMO_LOCATIONS["harbor-yard"]
    }


def test_harbor_employee_creates_harbor_ticket(client, login_as, db_session, lookup, ui):
    login_as("hugo.employee@harbor-yard.example")
    response = client.post(
        "/requests",
        data={
            "location_id": lookup.location_id("HY-LANE-3"),
            "priority_id": lookup.priority_id("HIGH"),
            "maintenance_type_id": lookup.type_id("CORRECTIVE"),
            "problem_description": "Lane 3 traffic light stuck on red.",
        },
        follow_redirects=True,
    )
    created = db_session.scalar(
        select(ServiceRequest).where(ServiceRequest.problem_description == "Lane 3 traffic light stuck on red.")
    )
    assert created.company.slug == "harbor-yard"
    assert ui("request.created", ticket=created.ticket_number) in response.text


# --- direct object access -----------------------------------------------------


def test_technician_cannot_log_work_on_other_company_request(client, login_as, db_session, lookup, ui):
    login_as("hana.tech@harbor-yard.example")
    campus_req = lookup.request("REQ-2026-0002", "campus-ops")
    before = len(campus_req.records)
    response = client.post(
        "/maintenance",
        data={
            "request_id": campus_req.id,
            "work_performed": "Should not be stored",
            "service_status_id": lookup.status_id("IN_PROGRESS"),
        },
    )
    assert ui("error.requestNotFound") in response.text
    assert campus_req.problem_description not in response.text
    db_session.expire_all()
    assert len(db_session.get(ServiceRequest, campus_req.id).records) == before


def test_log_form_does_not_preselect_other_company_request(client, login_as, lookup):
    login_as("hana.tech@harbor-yard.example")
    campus_req = lookup.request("REQ-2026-0002", "campus-ops")
    body = client.get(f"/maintenance/new?requestId={campus_req.id}").text
    assert campus_req.problem_description not in body


def test_manager_cannot_assign_other_company_request(client, login_as, db_session, lookup, ui):
    login_as(SAM, company="harbor-yard")
    campus_req = lookup.request("REQ-2026-0001", "campus-ops")
    body = client.post(
        "/requests/assign",
        data={"requestId": campus_req.id, "technicianId": lookup.user("HY-T-5001").id},
        follow_redirects=True,
    ).text
    assert ui("error.requestNotFound") in body
    db_session.expire_all()
    assert db_session.get(ServiceRequest, campus_req.id).assigned_technician_id is None


def test_require_by_id_is_scoped(db_session, lookup):
    harbor_req = lookup.request("REQ-2026-0002", "harbor-yard")
    assert request_service.require_by_id(db_session, harbor_req.id, company_id=harbor_req.company_id)
    with pytest.raises(BusinessError) as info:
        request_service.require_by_id(db_session, harbor_req.id, company_id=lookup.company("campus-ops").id)
    assert info.value.message_key == "error.requestNotFound"


# --- reports ------------------------------------------------------------------


def _stat_total(body: str) -> int:
    match = re.search(r'<div class="value">(\d+)</div>\s*<div class="label">', body)
    assert match
    return int(match.group(1))


def test_reports_are_company_scoped(client, login_as):
    login_as("morgan.manager@facility.example")
    campus = client.get("/reports").text
    assert _stat_total(campus) == len(CAMPUS_DESCRIPTIONS)
    assert "North gate" not in campus  # neither in rows nor in the location filter
    client.post("/logout")

    login_as(SAM, company="harbor-yard")
    harbor = client.get("/reports").text
    assert _stat_total(harbor) == len(HARBOR_DESCRIPTIONS)
    assert "North gate" in harbor and "Server room" not in harbor


def test_report_location_filter_from_other_company_is_empty(client, login_as, lookup, ui):
    login_as("morgan.manager@facility.example")
    body = client.get("/reports", params={"locationId": lookup.location_id("HY-GATE-N")}).text
    assert ui("report.empty") in body


def test_report_location_filter_includes_inactive_locations(client, login_as, db_session, lookup):
    location = db_session.get(Location, lookup.location_id("OFC-A-3-IT"))
    location.active = False
    db_session.commit()
    login_as("morgan.manager@facility.example")
    body = client.get("/reports").text
    assert f'<option value="{location.id}"' in body


# --- header -------------------------------------------------------------------


@pytest.mark.parametrize(
    ("login", "company", "name"),
    [
        ("morgan.manager@facility.example", "campus-ops", "Morgan Manager"),
        ("hana.tech@harbor-yard.example", "harbor-yard", "Hana Technician"),
        (SAM, "harbor-yard", "Sam Rivera"),
    ],
)
def test_header_shows_company_above_name(client, login_as, lookup, login, company, name):
    login_as(login, company=company)
    body = client.get("/").text
    card = body[body.index('class="session-card"'):]
    display = lookup.company(company).display_name
    assert f'data-company="{company}"' in card
    assert card.index(display) < card.index(name)


def test_header_company_label_is_translated(client, login_as, set_lang, ui):
    login_as("morgan.manager@facility.example")
    set_lang("es")
    assert ui("session.company", "es") in client.get("/").text


def test_login_page_has_no_company_header(client):
    assert 'class="session-company"' not in client.get("/login").text
