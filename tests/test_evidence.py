"""Photo evidence is served only through GET /evidence/{id} to users who may see the ticket."""

from __future__ import annotations

import io
import re
from pathlib import Path

import pytest
from sqlalchemy import select, text

from app.config import get_settings
from app.models import Account, AppUser, MaintenanceRecord, PhotoEvidence, Role
from app.seed import placeholder_png
from app.services import evidence as evidence_service
from app.services import requests as request_service

SAM = "sam.rivera@mail.example"
PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def _photos(db_session, ticket: str, company: str, lookup) -> list[PhotoEvidence]:
    req = lookup.request(ticket, company)
    return list(
        db_session.scalars(
            select(PhotoEvidence)
            .join(MaintenanceRecord)
            .where(MaintenanceRecord.request_id == req.id)
            .order_by(PhotoEvidence.id)
        )
    )


@pytest.fixture()
def campus_photo(db_session, lookup) -> PhotoEvidence:
    """Photo on campus REQ-2026-0002 (requester Ben E-1002, technician Tess T-2001)."""
    return _photos(db_session, "REQ-2026-0002", "campus-ops", lookup)[0]


@pytest.fixture()
def harbor_photo(db_session, lookup) -> PhotoEvidence:
    """Photo on harbor REQ-2026-0001 (requester Hugo, technician Hana)."""
    return _photos(db_session, "REQ-2026-0001", "harbor-yard", lookup)[0]


def _get(client, photo_id: int):
    return client.get(f"/evidence/{photo_id}", follow_redirects=False)


# --- no public mount ----------------------------------------------------------


def test_uploads_are_not_publicly_mounted(client, login_as, campus_photo):
    assert Path(get_settings().upload_dir, campus_photo.relative_path).is_file()  # the file exists ...
    assert client.get(f"/uploads/{campus_photo.relative_path}").status_code == 404  # ... but not at /uploads
    login_as("morgan.manager@facility.example")
    assert client.get(f"/uploads/{campus_photo.relative_path}").status_code == 404


def test_db_stores_a_relative_path_not_a_url(db_session):
    for photo in db_session.scalars(select(PhotoEvidence)):
        assert re.fullmatch(r"evidence/\d+/\d+/[\w.-]+\.(png|jpg|gif|webp)", photo.relative_path)
        assert "uploads" not in photo.relative_path


@pytest.mark.parametrize("bad", ["/etc/passwd", "../secret.png", "evidence/../../x.png", "", "evidence\\..\\x.png"])
def test_model_rejects_non_relative_paths(bad):
    with pytest.raises(ValueError):
        PhotoEvidence(record_id=1, file_name="x.png", relative_path=bad)


# --- authentication -------------------------------------------------------------


def test_anonymous_is_sent_to_login_and_back(client, campus_photo):
    response = _get(client, campus_photo.id)
    assert response.status_code == 303
    assert response.headers["location"] == f"/login?next=%2Fevidence%2F{campus_photo.id}"
    login = client.post(
        "/login",
        data={"company": "campus-ops", "login": "R-3001", "password": "OfficeDemo1!", "next": f"/evidence/{campus_photo.id}"},
        follow_redirects=False,
    )
    assert login.headers["location"] == f"/evidence/{campus_photo.id}"
    assert _get(client, campus_photo.id).status_code == 200


def test_served_file_and_headers(client, login_as, campus_photo):
    login_as("morgan.manager@facility.example")
    response = _get(client, campus_photo.id)
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/png"
    assert response.content == Path(get_settings().upload_dir, campus_photo.relative_path).read_bytes()
    assert response.content.startswith(PNG_SIGNATURE)
    assert response.headers["cache-control"] == "private, no-store"
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["content-disposition"].startswith("inline;")


# --- visibility: same rules as the request list ---------------------------------


@pytest.mark.parametrize(
    ("login", "company", "status"),
    [
        ("ben.employee@facility.example", "campus-ops", 200),  # requester
        ("tess.tech@facility.example", "campus-ops", 200),  # assigned technician
        ("morgan.manager@facility.example", "campus-ops", 200),  # manager of the company
        ("ada.employee@facility.example", "campus-ops", 404),  # other employee
        ("theo.tech@facility.example", "campus-ops", 404),  # other technician
        (SAM, "campus-ops", 404),  # Sam is only an employee at Campus Ops
        (SAM, "harbor-yard", 404),  # Sam is manager, but of the other company
        ("hana.tech@harbor-yard.example", "harbor-yard", 404),  # other company
    ],
)
def test_campus_photo_visibility(client, login_as, campus_photo, login, company, status):
    login_as(login, company=company)
    assert _get(client, campus_photo.id).status_code == status


@pytest.mark.parametrize(
    ("login", "company", "status"),
    [
        ("hugo.employee@harbor-yard.example", "harbor-yard", 200),
        ("hana.tech@harbor-yard.example", "harbor-yard", 200),
        (SAM, "harbor-yard", 200),
        ("morgan.manager@facility.example", "campus-ops", 404),
        ("tess.tech@facility.example", "campus-ops", 404),
    ],
)
def test_harbor_photo_visibility(client, login_as, harbor_photo, login, company, status):
    login_as(login, company=company)
    assert _get(client, harbor_photo.id).status_code == status


def test_refusals_do_not_reveal_existence(client, login_as, harbor_photo):
    login_as("morgan.manager@facility.example")
    cross = _get(client, harbor_photo.id)
    unknown = _get(client, 999_999)
    assert cross.status_code == unknown.status_code == 404
    assert cross.text == unknown.text
    assert client.get("/evidence/not-a-number").status_code == 404


def test_visibility_follows_reassignment(client, login_as, db_session, lookup, campus_photo):
    """The technician loses access once the ticket is assigned to someone else (list rule)."""
    req = lookup.request("REQ-2026-0002")
    req.assigned_technician_id = lookup.user("T-2002").id
    db_session.commit()
    login_as("tess.tech@facility.example")
    assert _get(client, campus_photo.id).status_code == 404


def test_unknown_role_sees_nothing(db_session, lookup, campus_photo):
    auditor_role = Role(code="AUDITOR", name="Auditor")
    db_session.add(auditor_role)
    db_session.flush()
    account = Account(email="auditor@facility.example")
    db_session.add(account)
    db_session.flush()
    auditor = AppUser(
        company_id=lookup.company("campus-ops").id, account_id=account.id, employee_number="A-1",
        first_name="Audit", last_name="Or", email="auditor@facility.example", role_id=auditor_role.id,
    )
    db_session.add(auditor)
    db_session.flush()
    assert request_service.list_requests(db_session, actor=auditor) == []
    assert evidence_service.get_visible_photo(db_session, actor=auditor, photo_id=campus_photo.id) is None
    db_session.rollback()


# --- file problems are 404s, never 500s or escapes ----------------------------


def test_missing_file_is_404(client, login_as, campus_photo, caplog):
    Path(get_settings().upload_dir, campus_photo.relative_path).unlink()
    login_as("morgan.manager@facility.example")
    with caplog.at_level("WARNING"):
        assert _get(client, campus_photo.id).status_code == 404
    assert "Evidence file missing" in caplog.text


def test_path_outside_upload_folder_is_refused(client, login_as, db_session, campus_photo, tmp_path, caplog):
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG_SIGNATURE + b"secret")
    # Bypass the model validator, as a corrupted/legacy row would.
    db_session.execute(
        text("UPDATE photo_evidence SET relative_path = :p WHERE id = :i"),
        {"p": "evidence/../../secret.png", "i": campus_photo.id},
    )
    db_session.commit()
    login_as("morgan.manager@facility.example")
    with caplog.at_level("WARNING"):
        response = _get(client, campus_photo.id)
    assert response.status_code == 404
    assert b"secret" not in response.content
    assert "outside the upload folder" in caplog.text


def test_non_allowlisted_extension_is_not_served(client, login_as, db_session, campus_photo):
    target = Path(get_settings().upload_dir, "evidence", "1", "2", "notes.html")
    target.write_text("<script>alert(1)</script>")
    db_session.execute(
        text("UPDATE photo_evidence SET relative_path = :p WHERE id = :i"),
        {"p": "evidence/1/2/notes.html", "i": campus_photo.id},
    )
    db_session.commit()
    login_as("morgan.manager@facility.example")
    assert _get(client, campus_photo.id).status_code == 404


# --- upload -> render -> download -----------------------------------------------


def test_uploaded_photo_round_trip(client, login_as, db_session, lookup):
    login_as("tess.tech@facility.example")
    req = lookup.request("REQ-2026-0002")
    image = placeholder_png((200, 40, 40), 32, 24)
    response = client.post(
        "/maintenance",
        data={
            "request_id": req.id,
            "work_performed": "Photo round trip",
            "service_status_id": lookup.status_id("IN_PROGRESS"),
            "evidence_description": "Round trip",
        },
        files={"evidenceFile": ("phone-photo.png", io.BytesIO(image), "image/png")},
        follow_redirects=False,
    )
    assert response.status_code == 303
    photo = db_session.scalar(select(PhotoEvidence).where(PhotoEvidence.description == "Round trip"))
    assert photo.relative_path.startswith(f"evidence/{req.company_id}/{req.id}/")
    assert photo.file_name != "phone-photo.png"  # server-generated name, not the client's

    detail = client.get(f"/requests/{req.id}").text
    url = f"/evidence/{photo.id}"
    assert f'src="{url}"' in detail  # URL built at render time from the id
    assert photo.relative_path not in detail  # storage path never exposed
    assert client.get(url).content == image


@pytest.mark.parametrize(
    ("name", "content_type", "payload"),
    [
        ("x.svg", "image/svg+xml", b"<svg onload=alert(1)>"),
        ("x.html", "text/html", b"<script>alert(1)</script>"),
        ("x.png", "image/png", b""),
    ],
)
def test_save_allowlist_unchanged(client, login_as, db_session, lookup, name, content_type, payload):
    login_as("tess.tech@facility.example")
    req = lookup.request("REQ-2026-0002")
    before = db_session.scalar(select(PhotoEvidence.id).order_by(PhotoEvidence.id.desc()))
    client.post(
        "/maintenance",
        data={"request_id": req.id, "work_performed": "x", "service_status_id": lookup.status_id("IN_PROGRESS")},
        files={"evidenceFile": (name, io.BytesIO(payload), content_type)},
    )
    assert db_session.scalar(select(PhotoEvidence.id).order_by(PhotoEvidence.id.desc())) == before


def test_allowlist_and_size_cap_values():
    assert evidence_service.ALLOWED_CONTENT_TYPES == {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/gif": ".gif",
        "image/webp": ".webp",
    }
    assert evidence_service.MAX_BYTES == 5 * 1024 * 1024


def test_seed_writes_real_images(db_session):
    for photo in db_session.scalars(select(PhotoEvidence)):
        data = Path(get_settings().upload_dir, photo.relative_path).read_bytes()
        assert data.startswith(PNG_SIGNATURE) and data[12:16] == b"IHDR"


# --- ticket detail page ---------------------------------------------------------


def test_detail_shows_work_log_and_photos(client, login_as, lookup, ui):
    login_as("cara.employee@facility.example")
    req = lookup.request("REQ-2026-0003")
    body = client.get(f"/requests/{req.id}").text
    assert req.problem_description in body
    assert ui("detail.records") in body and ui("detail.photos") in body
    assert "Replaced fluorescent tube" in body and "Electronic ballast" in body
    assert body.count('src="/evidence/') == 2


def test_detail_without_records(client, login_as, lookup, ui):
    login_as("ada.employee@facility.example")
    body = client.get(f"/requests/{lookup.request('REQ-2026-0001').id}").text
    assert ui("detail.noRecords") in body


@pytest.mark.parametrize(
    ("login", "company", "ticket", "owner"),
    [
        ("ada.employee@facility.example", "campus-ops", "REQ-2026-0002", "campus-ops"),  # not her ticket
        ("morgan.manager@facility.example", "campus-ops", "REQ-2026-0001", "harbor-yard"),  # other company
    ],
)
def test_detail_refuses_like_the_list(client, login_as, lookup, ui, login, company, ticket, owner):
    login_as(login, company=company)
    req = lookup.request(ticket, owner)
    response = client.get(f"/requests/{req.id}", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"] == "/requests"
    body = client.get("/requests").text
    assert ui("error.requestNotFound") in body
    assert req.problem_description not in body


def test_detail_requires_login(client, lookup):
    response = client.get(f"/requests/{lookup.request('REQ-2026-0002').id}", follow_redirects=False)
    assert response.status_code == 303 and response.headers["location"].startswith("/login")


def test_list_links_to_detail(client, login_as, lookup):
    login_as("morgan.manager@facility.example")
    req = lookup.request("REQ-2026-0003")
    assert f'<a href="/requests/{req.id}">REQ-2026-0003</a>' in client.get("/requests").text
