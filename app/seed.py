"""Demo data: companies, catalogs, people, and per-company locations and tickets.

Idempotent: db.init_db() runs create_all() and then seed_if_empty(), which only
seeds an empty database (no roles yet). Catalogs are global; locations, users,
tickets and work records belong to one company each.
"""

from __future__ import annotations

import struct
import zlib
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import hash_password
from app.config import get_settings
from app.models import (
    Account,
    AccountCredential,
    AppUser,
    Company,
    Location,
    MaintenanceRecord,
    MaintenanceType,
    MaterialUsed,
    PhotoEvidence,
    Priority,
    RequestStatus,
    Role,
    ServiceRequest,
)
from app.services.evidence import evidence_relative_path, upload_root

# (slug, display name, description). Exactly two demo tenants.
DEMO_COMPANIES: tuple[tuple[str, str, str], ...] = (
    ("campus-ops", "Campus Ops", "Office campus facilities: towers, offices, server rooms, parking."),
    ("harbor-yard", "Harbor Yard", "Yard and logistics: gates, cranes, warehouses, truck lanes."),
)

# Generic theme tokens per company (app/theme_tokens.py): an optional override
# layer on top of the company's front. The demo companies need none: Campus Ops
# keeps the default look and Harbor Yard's palette lives in its front theme hook
# (app/fronts/harbor-yard/templates/partials/theme.css).
DEMO_THEME_TOKENS: dict[str, dict[str, str]] = {
    "campus-ops": {},
    "harbor-yard": {},
}

# company slug -> (code, name, building, floor, area, description)
DEMO_LOCATIONS: dict[str, tuple[tuple[str, str, str, str, str, str], ...]] = {
    "campus-ops": (
        ("OFC-A-1-ADM", "Admin floor 1", "Tower A", "1", "Administration", "Administrative offices"),
        ("OFC-A-2-HR", "Human Resources", "Tower A", "2", "HR", "HR area"),
        ("OFC-B-GF-WH", "Ground-floor warehouse", "Tower B", "GF", "Warehouse", "General warehouse"),
        ("OFC-A-3-IT", "Server room", "Tower A", "3", "IT", "IT equipment room"),
        ("OFC-EXT-PKG", "Parking lot", "Exterior", "N/A", "Parking", "Parking area"),
    ),
    "harbor-yard": (
        ("HY-GATE-N", "North gate", "Gatehouse", "GF", "Access control", "Truck entry barriers and booth"),
        ("HY-CRANE-02", "Quay crane 2", "Quay", "N/A", "Cranes", "Ship-to-shore crane"),
        ("HY-WH-A", "Warehouse A", "Warehouse A", "GF", "Storage", "Cross-dock warehouse"),
        ("HY-LANE-3", "Truck lane 3", "Yard", "N/A", "Traffic", "Inbound truck lane"),
        ("HY-REEFER", "Reefer plug bank", "Yard", "N/A", "Power", "Refrigerated container sockets"),
    ),
}

# (company slug, employee number, first, last, login email, phone, role code).
# A person is identified by email: the same email in two companies is the same
# person (one Account, one password) with one membership per company.
DEMO_USERS: tuple[tuple[str, str, str, str, str, str, str], ...] = (
    ("campus-ops", "E-1001", "Ada", "Employee", "ada.employee@facility.example", "555-1001", "EMPLOYEE"),
    ("campus-ops", "E-1002", "Ben", "Employee", "ben.employee@facility.example", "555-1002", "EMPLOYEE"),
    ("campus-ops", "E-1003", "Cara", "Employee", "cara.employee@facility.example", "555-1003", "EMPLOYEE"),
    ("campus-ops", "T-2001", "Tess", "Technician", "tess.tech@facility.example", "555-2001", "TECHNICIAN"),
    ("campus-ops", "T-2002", "Theo", "Technician", "theo.tech@facility.example", "555-2002", "TECHNICIAN"),
    ("campus-ops", "R-3001", "Morgan", "Manager", "morgan.manager@facility.example", "555-3001", "AREA_MANAGER"),
    ("campus-ops", "E-1004", "Sam", "Rivera", "sam.rivera@mail.example", "555-6001", "EMPLOYEE"),
    ("harbor-yard", "HY-E-4001", "Hugo", "Employee", "hugo.employee@harbor-yard.example", "555-4001", "EMPLOYEE"),
    ("harbor-yard", "HY-T-5001", "Hana", "Technician", "hana.tech@harbor-yard.example", "555-5001", "TECHNICIAN"),
    ("harbor-yard", "HY-R-6001", "Sam", "Rivera", "sam.rivera@mail.example", "555-6001", "AREA_MANAGER"),
)

# Sample tickets per company. Ticket numbers are unique per company.
# (company, ticket, reported, location code, description, priority, type, status,
#  requester emp, technician emp or None, assigned_at or None)
DEMO_REQUESTS: tuple[tuple, ...] = (
    ("campus-ops", "REQ-2026-0001", datetime(2026, 10, 20, 9, 15), "OFC-A-1-ADM",
     "Water leak at the visitor restroom sink; puddle on the floor.",
     "HIGH", "CORRECTIVE", "PENDING", "E-1001", None, None),
    ("campus-ops", "REQ-2026-0002", datetime(2026, 10, 21, 11, 40), "OFC-A-3-IT",
     "Server-room AC is not cooling; temperature is elevated.",
     "CRITICAL", "CORRECTIVE", "IN_PROGRESS", "E-1002", "T-2001", datetime(2026, 10, 21, 12, 5)),
    ("campus-ops", "REQ-2026-0003", datetime(2026, 10, 18, 8, 5), "OFC-B-GF-WH",
     "Burned-out fluorescent lamp in the warehouse corridor.",
     "MEDIUM", "CORRECTIVE", "COMPLETED", "E-1003", "T-2002", datetime(2026, 10, 18, 9, 0)),
    ("campus-ops", "REQ-2026-0004", datetime(2026, 10, 22, 7, 30), "OFC-A-2-HR",
     "Quarterly inspection of fire extinguishers and evacuation signage.",
     "LOW", "PREVENTIVE", "IN_PROGRESS", "E-1001", "T-2002", datetime(2026, 10, 22, 8, 0)),
    ("campus-ops", "REQ-2026-0005", datetime(2026, 10, 23, 16, 20), "OFC-EXT-PKG",
     "Improvement request: install additional LED fixtures in a dark parking zone.",
     "MEDIUM", "IMPROVEMENT", "PENDING", "E-1003", None, None),
    ("harbor-yard", "REQ-2026-0001", datetime(2026, 10, 19, 6, 45), "HY-GATE-N",
     "North gate barrier arm is stuck in the up position.",
     "HIGH", "CORRECTIVE", "IN_PROGRESS", "HY-E-4001", "HY-T-5001", datetime(2026, 10, 19, 7, 0)),
    ("harbor-yard", "REQ-2026-0002", datetime(2026, 10, 21, 13, 10), "HY-REEFER",
     "Two reefer sockets trip the breaker when containers are plugged in.",
     "CRITICAL", "CORRECTIVE", "PENDING", "HY-E-4001", None, None),
    ("harbor-yard", "REQ-2026-0003", datetime(2026, 10, 16, 7, 0), "HY-CRANE-02",
     "Monthly lubrication and wire-rope inspection on quay crane 2.",
     "MEDIUM", "PREVENTIVE", "COMPLETED", "HY-R-6001", "HY-T-5001", datetime(2026, 10, 16, 7, 30)),
)

# (company, ticket, technician emp, attended, work, notes, status, materials,
#  photos as (file name, description, RGB of the generated placeholder image))
DEMO_RECORDS: tuple[tuple, ...] = (
    ("campus-ops", "REQ-2026-0002", "T-2001", datetime(2026, 10, 21, 14, 30),
     "AC diagnosis: clogged filter and low refrigerant. Filter cleaning started.",
     "Pending refrigerant recharge and temperature check.", "IN_PROGRESS",
     [("AC air filter 24x24", 1.0, "ea", "Clean/replace")],
     [("ac-filter.png", "Clogged filter", (120, 130, 110))]),
    ("campus-ops", "REQ-2026-0003", "T-2002", datetime(2026, 10, 18, 10, 15),
     "Replaced fluorescent tube and ballast. Power-on test OK.",
     "Area cleared with no electrical risk.", "COMPLETED",
     [("Fluorescent tube T8 32W", 1.0, "ea", None), ("Electronic ballast", 1.0, "ea", None)],
     [("before-lamp.png", "Burned-out lamp", (60, 60, 70)),
      ("after-lamp.png", "After replacement", (245, 225, 140))]),
    ("campus-ops", "REQ-2026-0004", "T-2002", datetime(2026, 10, 22, 9, 45),
     "Inspected 8 extinguishers in HR; 2 need recharge. Signage checked.",
     "Schedule recharge with external vendor.", "IN_PROGRESS",
     [("Inspection tag", 8.0, "ea", "Inspection date")], []),
    ("harbor-yard", "REQ-2026-0001", "HY-T-5001", datetime(2026, 10, 19, 9, 20),
     "Replaced the barrier limit switch; motor still overheats after 20 cycles.",
     "Order a replacement gear motor.", "IN_PROGRESS",
     [("Limit switch", 1.0, "ea", None)],
     [("gate-barrier.png", "Barrier arm stuck up", (47, 95, 138))]),
    ("harbor-yard", "REQ-2026-0003", "HY-T-5001", datetime(2026, 10, 16, 11, 0),
     "Greased slewing ring and hoist drum; wire rope within wear limits.",
     "Next inspection in 30 days.", "COMPLETED",
     [("Lithium grease", 2.5, "kg", None)], []),
)


def seed_if_empty(db: Session) -> None:
    if (db.scalar(select(func.count()).select_from(Role)) or 0) > 0:
        return
    _seed_companies(db)
    _seed_catalogs(db)
    _seed_locations(db)
    _seed_users(db)
    _seed_requests(db)
    db.commit()


def _seed_companies(db: Session) -> None:
    db.add_all(
        [
            Company(
                slug=slug,
                display_name=name,
                description=description,
                active=True,
                theme_tokens=DEMO_THEME_TOKENS.get(slug),
            )
            for slug, name, description in DEMO_COMPANIES
        ]
    )
    db.flush()


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


def _company_ids(db: Session) -> dict[str, int]:
    return {slug: cid for cid, slug in db.execute(select(Company.id, Company.slug))}


def _seed_locations(db: Session) -> None:
    companies = _company_ids(db)
    for slug, rows in DEMO_LOCATIONS.items():
        for code, name, building, floor, area, description in rows:
            db.add(
                Location(
                    company_id=companies[slug],
                    code=code,
                    name=name,
                    building=building,
                    floor=floor,
                    area=area,
                    description=description,
                )
            )
    db.flush()


def _seed_users(db: Session) -> None:
    """One Account (with one password) per distinct email; one membership per company row."""
    companies = _company_ids(db)
    roles = {code: rid for rid, code in db.execute(select(Role.id, Role.code))}
    password_hash = hash_password(get_settings().demo_password)
    accounts: dict[str, Account] = {}
    for slug, emp, first, last, email, phone, role_code in DEMO_USERS:
        account = accounts.get(email)
        if account is None:
            account = Account(email=email)
            account.credential = AccountCredential(password_hash=password_hash)
            db.add(account)
            db.flush()
            accounts[email] = account
        db.add(
            AppUser(
                company_id=companies[slug],
                account_id=account.id,
                employee_number=emp,
                first_name=first,
                last_name=last,
                email=email,
                phone=phone,
                role_id=roles[role_code],
                active=True,
            )
        )
    db.flush()


def _lookup(db: Session, model, company_id: int, **where):
    stmt = select(model.id).where(model.company_id == company_id)
    for column, value in where.items():
        stmt = stmt.where(getattr(model, column) == value)
    return db.scalar(stmt)


def _code_id(db: Session, model, code: str) -> int:
    return db.scalar(select(model.id).where(model.code == code))


def _seed_requests(db: Session) -> None:
    companies = _company_ids(db)
    for slug, ticket, reported, loc, desc, priority, mtype, status, requester, tech, assigned_at in DEMO_REQUESTS:
        cid = companies[slug]
        db.add(
            ServiceRequest(
                company_id=cid,
                ticket_number=ticket,
                reported_at=reported,
                location_id=_lookup(db, Location, cid, code=loc),
                problem_description=desc,
                priority_id=_code_id(db, Priority, priority),
                maintenance_type_id=_code_id(db, MaintenanceType, mtype),
                status_id=_code_id(db, RequestStatus, status),
                requester_id=_lookup(db, AppUser, cid, employee_number=requester),
                assigned_technician_id=_lookup(db, AppUser, cid, employee_number=tech) if tech else None,
                assigned_at=assigned_at,
                updated_at=reported,
            )
        )
    db.flush()

    for slug, ticket, tech, attended, work, notes, status, materials, photos in DEMO_RECORDS:
        cid = companies[slug]
        record = MaintenanceRecord(
            request_id=_lookup(db, ServiceRequest, cid, ticket_number=ticket),
            technician_id=_lookup(db, AppUser, cid, employee_number=tech),
            attended_at=attended,
            work_performed=work,
            notes=notes,
            service_status_id=_code_id(db, RequestStatus, status),
        )
        db.add(record)
        db.flush()
        for name, qty, unit, mnotes in materials:
            db.add(MaterialUsed(record_id=record.id, name=name, quantity=qty, unit=unit, notes=mnotes))
        for file_name, description, color in photos:
            relative = evidence_relative_path(cid, record.request_id, file_name)
            target = upload_root() / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(placeholder_png(color))
            db.add(
                PhotoEvidence(
                    record_id=record.id, file_name=file_name, relative_path=relative, description=description
                )
            )
    db.flush()


def placeholder_png(color: tuple[int, int, int], width: int = 240, height: int = 180) -> bytes:
    """A small striped PNG so demo evidence is a real, viewable image file."""
    light = tuple(min(255, c + 40) for c in color)
    rows = bytearray()
    for y in range(height):
        rows.append(0)  # PNG filter type: none
        for x in range(width):
            rows.extend(light if ((x + y) // 20) % 2 else color)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(bytes(rows))) + chunk(b"IEND", b"")
