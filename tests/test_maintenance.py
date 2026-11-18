from sqlalchemy import select

from app.models import MaintenanceRecord, RequestStatus, ServiceRequest


def test_technician_logs_work(client, login_as, db_session):
    login_as("tess.tech@facility.example")
    req = db_session.scalar(
        select(ServiceRequest).where(ServiceRequest.ticket_number == "REQ-2026-0002")
    )
    completed_id = db_session.scalar(
        select(RequestStatus.id).where(RequestStatus.code == "COMPLETED")
    )
    response = client.post(
        "/maintenance",
        data={
            "request_id": req.id,
            "work_performed": "Finished refrigerant recharge and verified temperature.",
            "notes": "Stable at 20C",
            "service_status_id": completed_id,
            "material_name": ["R-410A refrigerant"],
            "material_quantity": ["2"],
            "material_unit": ["kg"],
            "material_notes": ["top-up"],
            "evidence_description": "",
        },
        follow_redirects=False,
    )
    assert response.status_code == 303
    db_session.expire_all()
    records = list(
        db_session.scalars(
            select(MaintenanceRecord).where(MaintenanceRecord.request_id == req.id)
        )
    )
    assert len(records) >= 2
    refreshed = db_session.get(ServiceRequest, req.id)
    assert refreshed.status.code == "COMPLETED"


def test_technician_cannot_log_others_ticket(client, login_as, db_session):
    login_as("tess.tech@facility.example")
    # REQ-2026-0004 is assigned to T-2002
    req = db_session.scalar(
        select(ServiceRequest).where(ServiceRequest.ticket_number == "REQ-2026-0004")
    )
    in_progress_id = db_session.scalar(
        select(RequestStatus.id).where(RequestStatus.code == "IN_PROGRESS")
    )
    response = client.post(
        "/maintenance",
        data={
            "request_id": req.id,
            "work_performed": "Should fail",
            "notes": "",
            "service_status_id": in_progress_id,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"only log work" in response.content.lower() or b"assigned to you" in response.content.lower()


def test_employee_forbidden_from_log_form(client, login_as):
    login_as("ada.employee@facility.example")
    response = client.get("/maintenance/new", follow_redirects=False)
    assert response.status_code == 303
