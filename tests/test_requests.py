import pytest
from sqlalchemy import func, select

from app.models import ServiceRequest
from app.services import requests as request_service


def _new_request_form(lookup, **overrides):
    data = {
        "location_id": lookup.location_id("OFC-A-1-ADM"),
        "priority_id": lookup.priority_id("HIGH"),
        "maintenance_type_id": lookup.type_id("CORRECTIVE"),
        "problem_description": "Broken chair in lobby.",
    }
    data.update(overrides)
    return data


def _count(db_session) -> int:
    return db_session.scalar(select(func.count()).select_from(ServiceRequest))


# --- create -------------------------------------------------------------------


def test_employee_creates_request(client, login_as, db_session, lookup, ui):
    login_as("ada.employee@facility.example")
    form_page = client.get("/requests/new")
    assert form_page.status_code == 200
    assert ui("request.heading") in form_page.text

    before = _count(db_session)
    response = client.post("/requests", data=_new_request_form(lookup), follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/requests"
    assert _count(db_session) == before + 1

    created = db_session.scalar(select(ServiceRequest).order_by(ServiceRequest.id.desc()).limit(1))
    assert created.ticket_number.startswith("REQ-")
    assert created.status.code == "PENDING"
    assert created.requester.employee_number == "E-1001"

    listing = client.get("/requests")
    assert ui("request.created", ticket=created.ticket_number) in listing.text


def test_manager_can_create_request(client, login_as, lookup):
    login_as("morgan.manager@facility.example")
    response = client.post("/requests", data=_new_request_form(lookup), follow_redirects=False)
    assert response.status_code == 303


def test_blank_description_rerenders_with_translated_error(client, login_as, set_lang, db_session, lookup, ui):
    login_as("ada.employee@facility.example")
    before = _count(db_session)
    for lang in ("en", "es"):
        set_lang(lang)
        response = client.post("/requests", data=_new_request_form(lookup, problem_description="   "))
        assert response.status_code == 200
        assert ui("error.descriptionRequired", lang) in response.text
        assert ui("request.heading", lang) in response.text
    assert _count(db_session) == before


def test_technician_cannot_open_new_request(client, login_as, ui):
    login_as("T-2001")
    response = client.get("/requests/new", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert ui("forbidden") in client.get("/").text


def test_technician_cannot_post_request(client, login_as, db_session, lookup, ui):
    login_as("T-2001")
    before = _count(db_session)
    response = client.post("/requests", data=_new_request_form(lookup))
    assert ui("error.onlyRequesters") in response.text
    assert _count(db_session) == before


# --- list (role-scoped) -------------------------------------------------------


def test_employee_sees_only_own_tickets(client, login_as, ui):
    login_as("ada.employee@facility.example")
    body = client.get("/requests").text
    assert "REQ-2026-0001" in body
    assert "REQ-2026-0004" in body
    assert "REQ-2026-0002" not in body


def test_technician_sees_only_assigned(client, login_as):
    login_as("tess.tech@facility.example")
    body = client.get("/requests").text
    assert "REQ-2026-0002" in body
    assert "REQ-2026-0003" not in body
    assert 'href="/maintenance/new?requestId=' in body


def test_manager_sees_all_and_can_assign(client, login_as, ui):
    login_as("morgan.manager@facility.example")
    body = client.get("/requests").text
    for n in range(1, 6):
        assert f"REQ-2026-000{n}" in body
    assert 'action="/requests/assign"' in body
    assert ui("list.closed") in body  # completed ticket shows closed instead of assign form


def test_list_search_and_filters(client, login_as, lookup, ui):
    login_as("morgan.manager@facility.example")

    by_text = client.get("/requests", params={"q": "server"}).text
    assert "REQ-2026-0002" in by_text and "REQ-2026-0001" not in by_text

    by_location_code = client.get("/requests", params={"q": "ofc-ext"}).text
    assert "REQ-2026-0005" in by_location_code and "REQ-2026-0002" not in by_location_code

    by_status = client.get("/requests", params={"statusId": lookup.status_id("COMPLETED")}).text
    assert "REQ-2026-0003" in by_status and "REQ-2026-0001" not in by_status

    by_type = client.get("/requests", params={"maintenanceTypeId": lookup.type_id("PREVENTIVE")}).text
    assert "REQ-2026-0004" in by_type and "REQ-2026-0002" not in by_type

    empty = client.get("/requests", params={"q": "no-such-thing"}).text
    assert ui("list.empty") in empty


# --- assign -------------------------------------------------------------------


def test_manager_assigns_technician(client, login_as, db_session, lookup, ui):
    login_as("morgan.manager@facility.example")
    pending = lookup.request("REQ-2026-0001")
    tess = lookup.user("T-2001")
    response = client.post(
        "/requests/assign",
        data={"requestId": pending.id, "technicianId": tess.id},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.expire_all()
    refreshed = db_session.get(ServiceRequest, pending.id)
    assert refreshed.assigned_technician_id == tess.id
    assert refreshed.assigned_at is not None
    assert refreshed.status.code == "IN_PROGRESS"
    assert ui("list.assigned") in client.get("/requests").text


def _assign(client, request_id: int, technician_id: int) -> str:
    return client.post(
        "/requests/assign",
        data={"requestId": request_id, "technicianId": technician_id},
        follow_redirects=True,
    ).text


def test_assign_requires_technician_role(client, login_as, set_lang, db_session, lookup, ui):
    login_as("morgan.manager@facility.example")
    set_lang("es")
    pending = lookup.request("REQ-2026-0001")
    body = _assign(client, pending.id, lookup.user("E-1002").id)
    assert ui("error.assigneeMustBeTechnician", "es") in body
    db_session.expire_all()
    assert db_session.get(ServiceRequest, pending.id).assigned_technician_id is None


def test_assign_closed_request_rejected(client, login_as, lookup, ui):
    login_as("morgan.manager@facility.example")
    body = _assign(client, lookup.request("REQ-2026-0003").id, lookup.user("T-2001").id)
    assert ui("error.assignClosed") in body


def test_assign_unknown_request_or_user(client, login_as, lookup, ui):
    login_as("morgan.manager@facility.example")
    assert ui("error.requestNotFound") in _assign(client, 999_999, lookup.user("T-2001").id)
    assert ui("error.userNotFound") in _assign(client, lookup.request("REQ-2026-0001").id, 999_999)


def test_only_managers_assign(client, login_as, lookup, ui):
    login_as("ada.employee@facility.example")
    body = _assign(client, lookup.request("REQ-2026-0001").id, lookup.user("T-2001").id)
    assert ui("error.onlyManagersAssign") in body


# --- create: validation (BusinessError with a message key, never a 500) ------


@pytest.mark.parametrize(
    ("overrides", "key"),
    [
        ({"location_id": ""}, "error.locationInvalid"),
        ({"location_id": "abc"}, "error.locationInvalid"),
        ({"location_id": "999999"}, "error.locationInvalid"),
        ({"priority_id": ""}, "error.priorityInvalid"),
        ({"priority_id": "999999"}, "error.priorityInvalid"),
        ({"maintenance_type_id": "x"}, "error.typeInvalid"),
        ({"maintenance_type_id": "999999"}, "error.typeInvalid"),
    ],
)
def test_invalid_catalog_values_are_form_errors(client, login_as, db_session, lookup, ui, overrides, key):
    login_as("ada.employee@facility.example")
    before = _count(db_session)
    response = client.post("/requests", data=_new_request_form(lookup, **overrides))
    assert response.status_code == 200
    assert ui(key) in response.text
    assert _count(db_session) == before


def test_other_company_location_is_rejected_like_a_missing_one(client, login_as, db_session, lookup, ui):
    login_as("ada.employee@facility.example")
    before = _count(db_session)
    harbor_gate = lookup.location_id("HY-GATE-N")
    response = client.post("/requests", data=_new_request_form(lookup, location_id=harbor_gate))
    assert ui("error.locationInvalid") in response.text
    assert _count(db_session) == before


def test_inactive_location_is_rejected(client, login_as, set_lang, db_session, lookup, ui):
    from app.models import Location

    location = db_session.get(Location, lookup.location_id("OFC-EXT-PKG"))
    location.active = False
    db_session.commit()
    login_as("ada.employee@facility.example")
    form_page = client.get("/requests/new").text
    assert "OFC-EXT-PKG" not in form_page  # not offered any more
    set_lang("es")
    response = client.post("/requests", data=_new_request_form(lookup, location_id=location.id))
    assert ui("error.locationInactive", "es") in response.text


def test_create_uses_actor_company(db_session, lookup):
    created = request_service.create_request(
        db_session,
        actor=lookup.user("HY-E-4001"),
        location_id=lookup.location_id("HY-WH-A"),
        problem_description="Dock door 4 does not close.",
        priority_id=lookup.priority_id("MEDIUM"),
        maintenance_type_id=lookup.type_id("CORRECTIVE"),
    )
    assert created.company.slug == "harbor-yard"
    assert created.location.company_id == created.company_id == created.requester.company_id


# --- ticket numbers -----------------------------------------------------------


def _create(db_session, lookup, emp="E-1001", location="OFC-A-1-ADM"):
    return request_service.create_request(
        db_session,
        actor=lookup.user(emp),
        location_id=lookup.location_id(location),
        problem_description="Ticket numbering check",
        priority_id=lookup.priority_id("LOW"),
        maintenance_type_id=lookup.type_id("CORRECTIVE"),
    )


def test_ticket_sequence_is_per_company(db_session, lookup):
    campus, harbor = lookup.company("campus-ops").id, lookup.company("harbor-yard").id
    assert request_service.next_ticket_number(db_session, campus, 2026) == "REQ-2026-0006"
    assert request_service.next_ticket_number(db_session, harbor, 2026) == "REQ-2026-0004"
    assert request_service.next_ticket_number(db_session, campus, 2031) == "REQ-2031-0001"


def test_counter_continues_after_seeded_tickets(db_session, lookup, monkeypatch):
    from datetime import datetime

    # Pin "now" to the seed year so the counter has to start after REQ-2026-0005.
    monkeypatch.setattr(request_service, "utcnow", lambda: datetime(2026, 11, 1, 9, 0))
    first = _create(db_session, lookup)
    second = _create(db_session, lookup)
    harbor = _create(db_session, lookup, emp="HY-E-4001", location="HY-GATE-N")
    assert (first.ticket_number, second.ticket_number) == ("REQ-2026-0006", "REQ-2026-0007")
    assert harbor.ticket_number == "REQ-2026-0004"


def test_malformed_ticket_does_not_restart_sequence(client, login_as, db_session, lookup):
    """A non-numeric latest ticket used to reset the sequence to 0001 (collision -> 500)."""
    lookup.request("REQ-2026-0003").ticket_number = "REQ-2026-X1"
    db_session.commit()
    campus = lookup.company("campus-ops").id
    assert request_service.highest_ticket_number(db_session, campus, 2026) == 5

    login_as("ada.employee@facility.example")
    response = client.post("/requests", data=_new_request_form(lookup), follow_redirects=False)
    assert response.status_code == 303


def test_ticket_sequence_is_numeric_not_lexical(db_session, lookup):
    lookup.request("REQ-2026-0005").ticket_number = "REQ-2026-10000"
    db_session.commit()
    assert request_service.next_ticket_number(db_session, lookup.company("campus-ops").id, 2026) == "REQ-2026-10001"


def test_non_ascii_digits_are_ignored(db_session, lookup):
    lookup.request("REQ-2026-0005").ticket_number = "REQ-2026-\u00b2"
    db_session.commit()
    assert request_service.highest_ticket_number(db_session, lookup.company("campus-ops").id, 2026) == 4


def test_counter_catches_up_with_tickets_created_outside_it(db_session, lookup):
    """Counter behind the data (import/manual insert): the clash is retried, not a 500."""
    from app.models import TicketCounter

    first = _create(db_session, lookup)
    year = int(first.ticket_number.split("-")[1])
    campus = lookup.company("campus-ops").id
    db_session.get(TicketCounter, (campus, year)).last_number = 0  # simulate a stale counter
    db_session.commit()
    second = _create(db_session, lookup)
    assert second.ticket_number != first.ticket_number
    assert int(second.ticket_number.rsplit("-", 1)[1]) == int(first.ticket_number.rsplit("-", 1)[1]) + 1


def test_concurrent_allocations_get_distinct_numbers(db_session, lookup):
    """Two sessions allocate at once: the second waits on the counter row, then takes the next number."""
    import threading

    from sqlalchemy.orm import sessionmaker

    from app import db as db_module

    campus = lookup.company("campus-ops").id
    Session = sessionmaker(bind=db_module.engine, autoflush=False)
    first_session, second_session = Session(), Session()
    results: dict[str, str] = {}
    try:
        results["first"] = request_service.allocate_ticket_number(first_session, campus, 2030)  # holds the lock

        def second():
            results["second"] = request_service.allocate_ticket_number(second_session, campus, 2030)
            second_session.commit()

        worker = threading.Thread(target=second)
        worker.start()
        worker.join(timeout=0.5)
        assert worker.is_alive()  # blocked behind the first transaction
        first_session.commit()
        worker.join(timeout=10)
        assert not worker.is_alive()
    finally:
        first_session.close()
        second_session.close()
    assert {results["first"], results["second"]} == {"REQ-2030-0001", "REQ-2030-0002"}


def test_ticket_conflict_after_retries_is_reported(client, login_as, db_session, lookup, monkeypatch, ui):
    monkeypatch.setattr(request_service, "allocate_ticket_number", lambda db, cid, year=None: "REQ-2026-0001")
    login_as("ada.employee@facility.example")
    before = _count(db_session)
    response = client.post("/requests", data=_new_request_form(lookup))
    assert response.status_code == 200
    assert ui("error.ticketConflict") in response.text
    assert _count(db_session) == before


def test_counter_row_race_is_retried(db_session, lookup, monkeypatch):
    """First allocation fails inside the counter insert (ticket unknown): resync and retry."""
    from sqlalchemy.exc import IntegrityError

    real = request_service.allocate_ticket_number
    calls = []

    def flaky(db, company_id, year=None):
        calls.append(1)
        if len(calls) == 1:
            raise IntegrityError("INSERT INTO ticket_counter", {}, Exception("UNIQUE constraint failed"))
        return real(db, company_id, year)

    monkeypatch.setattr(request_service, "allocate_ticket_number", flaky)
    created = _create(db_session, lookup)
    assert len(calls) == 2 and created.ticket_number.startswith("REQ-")


def test_other_integrity_errors_become_business_errors(client, login_as, db_session, lookup, monkeypatch, ui):
    """A constraint failure that is not about the ticket number is reported, not a 500."""
    monkeypatch.setattr(request_service, "allocate_ticket_number", lambda db, cid, year=None: "REQ-2026-0001")
    monkeypatch.setattr(request_service, "_ticket_taken", lambda db, cid, ticket: False)
    login_as("ada.employee@facility.example")
    response = client.post("/requests", data=_new_request_form(lookup))
    assert response.status_code == 200
    assert ui("error.requestNotSaved") in response.text


def test_resync_tolerates_concurrent_counter_insert(db_session, lookup, monkeypatch):
    from sqlalchemy.exc import IntegrityError

    campus = lookup.company("campus-ops").id

    def boom():
        raise IntegrityError("INSERT INTO ticket_counter", {}, Exception("UNIQUE"))

    monkeypatch.setattr(db_session, "commit", boom)
    request_service._resync_counter(db_session, campus, 2040)  # must not raise
