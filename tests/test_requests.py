from sqlalchemy import select

from app.models import ServiceRequest


def test_employee_creates_request(client, login_as, db_session):
    login_as("ada.employee@facility.example")
    form_page = client.get("/requests/new")
    assert form_page.status_code == 200

    response = client.post(
        "/requests",
        data={
            "location_id": 1,
            "priority_id": 3,
            "maintenance_type_id": 1,
            "problem_description": "Broken chair in lobby.",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    tickets = list(db_session.scalars(select(ServiceRequest.ticket_number)))
    assert any(t.startswith("REQ-") for t in tickets)


def test_technician_cannot_open_new_request(client, login_as):
    login_as("T-2001")
    response = client.get("/requests/new", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


def test_employee_sees_only_own_tickets(client, login_as):
    login_as("ada.employee@facility.example")
    response = client.get("/requests")
    assert response.status_code == 200
    body = response.text
    assert "REQ-2026-0001" in body
    assert "REQ-2026-0002" not in body


def test_manager_assigns_technician(client, login_as, db_session):
    login_as("morgan.manager@facility.example")
    pending = db_session.scalar(
        select(ServiceRequest).where(ServiceRequest.ticket_number == "REQ-2026-0001")
    )
    assert pending is not None
    response = client.post(
        "/requests/assign",
        data={"requestId": pending.id, "technicianId": 4},
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.expire_all()
    refreshed = db_session.get(ServiceRequest, pending.id)
    assert refreshed.assigned_technician_id == 4
    assert refreshed.status.code == "IN_PROGRESS"
