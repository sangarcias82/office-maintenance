"""Initial catalog, users, and sample tickets. Idempotent: runs only on empty DB."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import get_settings
from app.models import (
    AppUser,
    Location,
    MaintenanceRecord,
    MaintenanceType,
    MaterialUsed,
    PhotoEvidence,
    Priority,
    RequestStatus,
    Role,
    ServiceRequest,
    UserCredential,
)


def seed_if_empty(db: Session) -> None:
    count = db.scalar(select(func.count()).select_from(Role)) or 0
    if count > 0:
        return
    _seed_catalogs(db)
    _seed_locations(db)
    _seed_users(db)
    _seed_requests(db)
    db.commit()


def _seed_catalogs(db: Session) -> None:
    db.add_all(
        [
            Role(code="EMPLOYEE", name="Employee", description="Reports failures from their work area"),
            Role(code="TECHNICIAN", name="Technician", description="Handles and documents maintenance work"),
            Role(code="AREA_MANAGER", name="Area manager", description="Assigns work, tracks progress, views reports"),
        ]
    )
    db.flush()
    db.add_all(
        [
            Priority(code="LOW", name="Low", level=1, description="Can be scheduled without urgency"),
            Priority(code="MEDIUM", name="Medium", level=2, description="Short-term attention required"),
            Priority(code="HIGH", name="High", level=3, description="Impacts operations; address soon"),
            Priority(code="CRITICAL", name="Critical", level=4, description="Risk or outage; immediate attention"),
        ]
    )
    db.add_all(
        [
            RequestStatus(code="PENDING", name="Pending", is_final=False, description="Registered, not started"),
            RequestStatus(code="IN_PROGRESS", name="In progress", is_final=False, description="Assigned or in progress"),
            RequestStatus(code="COMPLETED", name="Completed", is_final=True, description="Service finished"),
            RequestStatus(code="CANCELLED", name="Cancelled", is_final=True, description="Request cancelled"),
        ]
    )
    db.add_all(
        [
            MaintenanceType(code="CORRECTIVE", name="Corrective", description="Response to a reported failure"),
            MaintenanceType(code="PREVENTIVE", name="Preventive", description="Scheduled maintenance"),
            MaintenanceType(code="IMPROVEMENT", name="Improvement", description="Facility adjustment or upgrade"),
        ]
    )
    db.flush()


def _seed_locations(db: Session) -> None:
    db.add_all(
        [
            Location(
                code="OFC-A-1-ADM",
                name="Admin floor 1",
                building="Tower A",
                floor="1",
                area="Administration",
                description="Administrative offices",
            ),
            Location(
                code="OFC-A-2-HR",
                name="Human Resources",
                building="Tower A",
                floor="2",
                area="HR",
                description="HR area",
            ),
            Location(
                code="OFC-B-GF-WH",
                name="Ground-floor warehouse",
                building="Tower B",
                floor="GF",
                area="Warehouse",
                description="General warehouse",
            ),
            Location(
                code="OFC-A-3-IT",
                name="Server room",
                building="Tower A",
                floor="3",
                area="IT",
                description="IT equipment room",
            ),
            Location(
                code="OFC-EXT-PKG",
                name="Parking lot",
                building="Exterior",
                floor="N/A",
                area="Parking",
                description="Parking area",
            ),
        ]
    )
    db.flush()


def _role_id(db: Session, code: str) -> int:
    return db.scalar(select(Role.id).where(Role.code == code))


def _seed_users(db: Session) -> None:
    password = get_settings().demo_password
    password_hash = hash_password(password)
    users = [
        ("E-1001", "Ada", "Employee", "ada.employee@facility.example", "555-1001", "EMPLOYEE"),
        ("E-1002", "Ben", "Employee", "ben.employee@facility.example", "555-1002", "EMPLOYEE"),
        ("E-1003", "Cara", "Employee", "cara.employee@facility.example", "555-1003", "EMPLOYEE"),
        ("T-2001", "Tess", "Technician", "tess.tech@facility.example", "555-2001", "TECHNICIAN"),
        ("T-2002", "Theo", "Technician", "theo.tech@facility.example", "555-2002", "TECHNICIAN"),
        ("R-3001", "Morgan", "Manager", "morgan.manager@facility.example", "555-3001", "AREA_MANAGER"),
    ]
    for emp, first, last, email, phone, role_code in users:
        user = AppUser(
            employee_number=emp,
            first_name=first,
            last_name=last,
            email=email,
            phone=phone,
            role_id=_role_id(db, role_code),
            active=True,
        )
        db.add(user)
        db.flush()
        db.add(UserCredential(user_id=user.id, password_hash=password_hash))
    db.flush()


def _id(db: Session, model, code: str) -> int:
    return db.scalar(select(model.id).where(model.code == code))


def _user_id(db: Session, emp: str) -> int:
    return db.scalar(select(AppUser.id).where(AppUser.employee_number == emp))


def _seed_requests(db: Session) -> None:
    rows = [
        {
            "ticket": "REQ-2026-0001",
            "reported": datetime(2026, 10, 20, 9, 15),
            "loc": "OFC-A-1-ADM",
            "desc": "Water leak at the visitor restroom sink; puddle on the floor.",
            "priority": "HIGH",
            "mtype": "CORRECTIVE",
            "status": "PENDING",
            "requester": "E-1001",
            "tech": None,
            "assigned_at": None,
        },
        {
            "ticket": "REQ-2026-0002",
            "reported": datetime(2026, 10, 21, 11, 40),
            "loc": "OFC-A-3-IT",
            "desc": "Server-room AC is not cooling; temperature is elevated.",
            "priority": "CRITICAL",
            "mtype": "CORRECTIVE",
            "status": "IN_PROGRESS",
            "requester": "E-1002",
            "tech": "T-2001",
            "assigned_at": datetime(2026, 10, 21, 12, 5),
        },
        {
            "ticket": "REQ-2026-0003",
            "reported": datetime(2026, 10, 18, 8, 5),
            "loc": "OFC-B-GF-WH",
            "desc": "Burned-out fluorescent lamp in the warehouse corridor.",
            "priority": "MEDIUM",
            "mtype": "CORRECTIVE",
            "status": "COMPLETED",
            "requester": "E-1003",
            "tech": "T-2002",
            "assigned_at": datetime(2026, 10, 18, 9, 0),
        },
        {
            "ticket": "REQ-2026-0004",
            "reported": datetime(2026, 10, 22, 7, 30),
            "loc": "OFC-A-2-HR",
            "desc": "Quarterly inspection of fire extinguishers and evacuation signage.",
            "priority": "LOW",
            "mtype": "PREVENTIVE",
            "status": "IN_PROGRESS",
            "requester": "E-1001",
            "tech": "T-2002",
            "assigned_at": datetime(2026, 10, 22, 8, 0),
        },
        {
            "ticket": "REQ-2026-0005",
            "reported": datetime(2026, 10, 23, 16, 20),
            "loc": "OFC-EXT-PKG",
            "desc": "Improvement request: install additional LED fixtures in a dark parking zone.",
            "priority": "MEDIUM",
            "mtype": "IMPROVEMENT",
            "status": "PENDING",
            "requester": "E-1003",
            "tech": None,
            "assigned_at": None,
        },
    ]

    for row in rows:
        tech_id = _user_id(db, row["tech"]) if row["tech"] else None
        req = ServiceRequest(
            ticket_number=row["ticket"],
            reported_at=row["reported"],
            location_id=_id(db, Location, row["loc"]),
            problem_description=row["desc"],
            priority_id=_id(db, Priority, row["priority"]),
            maintenance_type_id=_id(db, MaintenanceType, row["mtype"]),
            status_id=_id(db, RequestStatus, row["status"]),
            requester_id=_user_id(db, row["requester"]),
            assigned_technician_id=tech_id,
            assigned_at=row["assigned_at"],
            updated_at=row["reported"],
        )
        db.add(req)
    db.flush()

    records = [
        (
            "REQ-2026-0002",
            "T-2001",
            datetime(2026, 10, 21, 14, 30),
            "AC diagnosis: clogged filter and low refrigerant. Filter cleaning started.",
            "Pending refrigerant recharge and temperature check.",
            "IN_PROGRESS",
            [("AC air filter 24x24", 1.0, "ea", "Clean/replace")],
            [("ac-filter.jpg", "/uploads/evidence/demo/ac-filter.jpg", "Clogged filter")],
        ),
        (
            "REQ-2026-0003",
            "T-2002",
            datetime(2026, 10, 18, 10, 15),
            "Replaced fluorescent tube and ballast. Power-on test OK.",
            "Area cleared with no electrical risk.",
            "COMPLETED",
            [
                ("Fluorescent tube T8 32W", 1.0, "ea", None),
                ("Electronic ballast", 1.0, "ea", None),
            ],
            [
                ("before-lamp.jpg", "/uploads/evidence/demo/before-lamp.jpg", "Burned-out lamp"),
                ("after-lamp.jpg", "/uploads/evidence/demo/after-lamp.jpg", "After replacement"),
            ],
        ),
        (
            "REQ-2026-0004",
            "T-2002",
            datetime(2026, 10, 22, 9, 45),
            "Inspected 8 extinguishers in HR; 2 need recharge. Signage checked.",
            "Schedule recharge with external vendor.",
            "IN_PROGRESS",
            [("Inspection tag", 8.0, "ea", "Inspection date")],
            [],
        ),
    ]

    for ticket, tech, attended, work, notes, status, materials, photos in records:
        req_id = db.scalar(select(ServiceRequest.id).where(ServiceRequest.ticket_number == ticket))
        record = MaintenanceRecord(
            request_id=req_id,
            technician_id=_user_id(db, tech),
            attended_at=attended,
            work_performed=work,
            notes=notes,
            service_status_id=_id(db, RequestStatus, status),
        )
        db.add(record)
        db.flush()
        for name, qty, unit, mnotes in materials:
            db.add(
                MaterialUsed(record_id=record.id, name=name, quantity=qty, unit=unit, notes=mnotes)
            )
        for file_name, path, desc in photos:
            db.add(
                PhotoEvidence(
                    record_id=record.id,
                    file_name=file_name,
                    path_or_url=path,
                    description=desc,
                )
            )
