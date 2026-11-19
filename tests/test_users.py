"""Users belong to one company; login identity never crosses companies."""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.auth import authenticate, hash_password
from app.models import Account, AccountCredential, AppUser, Company, Role, ServiceRequest
from app.seed import DEMO_USERS
from app.services import users as user_service

DEMO_PASSWORD = "OfficeDemo1!"
OTHER = {"campus-ops": "harbor-yard", "harbor-yard": "campus-ops"}
USER_IDS = [u[1] for u in DEMO_USERS]
_EMAIL_COUNT = {u[4]: sum(1 for x in DEMO_USERS if x[4] == u[4]) for u in DEMO_USERS}
# People with a membership in only one company (Sam Rivera works in both).
SINGLE_COMPANY_IDS = [u[1] for u in DEMO_USERS if _EMAIL_COUNT[u[4]] == 1]


def _company(db_session, slug: str) -> Company:
    return db_session.scalar(select(Company).where(Company.slug == slug))


def _add_user(
    db_session,
    *,
    company: str,
    email: str,
    emp: str,
    first: str,
    password: str = DEMO_PASSWORD,
    account: Account | None = None,
    role: str = "EMPLOYEE",
):
    """Add a membership; creates a new person (Account + password) unless one is given."""
    if account is None:
        account = Account(email=f"{emp.lower()}@accounts.example")
        account.credential = AccountCredential(password_hash=hash_password(password))
        db_session.add(account)
        db_session.flush()
    user = AppUser(
        company_id=_company(db_session, company).id,
        account_id=account.id,
        employee_number=emp,
        first_name=first,
        last_name="Twin",
        email=email,
        role_id=db_session.scalar(select(Role.id).where(Role.code == role)),
    )
    db_session.add(user)
    db_session.commit()
    return user


# --- seed ---------------------------------------------------------------------


def test_every_seeded_user_has_a_company(db_session):
    users = list(db_session.scalars(select(AppUser)))
    assert len(users) == len(DEMO_USERS)
    expected = {emp: slug for slug, emp, *_ in DEMO_USERS}
    for user in users:
        assert user.company_id is not None
        assert user.company.slug == expected[user.employee_number]


def test_demo_users_are_split_across_companies(db_session):
    roles_by_company: dict[str, set[str]] = {}
    for user in db_session.scalars(select(AppUser)):
        roles_by_company.setdefault(user.company.slug, set()).add(user.role_code)
    assert roles_by_company["campus-ops"] == {"EMPLOYEE", "TECHNICIAN", "AREA_MANAGER"}
    assert roles_by_company["harbor-yard"] == {"EMPLOYEE", "TECHNICIAN", "AREA_MANAGER"}


def test_one_account_per_person(db_session):
    """Memberships with the same email share one Account (one person, one password)."""
    emails = {u[4] for u in DEMO_USERS}
    assert db_session.scalar(select(func.count()).select_from(Account)) == len(emails)
    for user in db_session.scalars(select(AppUser)):
        assert user.account.email == user.email


# --- login with the demo password ---------------------------------------------


@pytest.mark.parametrize("emp", USER_IDS)
def test_each_demo_user_logs_in_to_own_company(client, db_session, emp):
    slug, _, first, last, email, *_ = next(u for u in DEMO_USERS if u[1] == emp)
    for login in (email, emp):
        client.post("/logout")
        response = client.post(
            "/login",
            data={"company": slug, "login": login, "password": DEMO_PASSWORD, "next": "/"},
            follow_redirects=False,
        )
        assert response.status_code == 303, (slug, login)
        assert f"{first} {last}" in client.get("/").text


@pytest.mark.parametrize("emp", SINGLE_COMPANY_IDS)
def test_demo_user_cannot_log_in_to_other_company(db_session, emp):
    slug, _, _, _, email, *_ = next(u for u in DEMO_USERS if u[1] == emp)
    assert authenticate(db_session, slug, email, DEMO_PASSWORD) is not None
    assert authenticate(db_session, OTHER[slug], email, DEMO_PASSWORD) is None
    assert authenticate(db_session, OTHER[slug], emp, DEMO_PASSWORD) is None


# --- identifiers unique per company -------------------------------------------


# --- one employee, two companies ----------------------------------------------

SAM = "sam.rivera@mail.example"


def test_same_employee_works_in_two_companies(db_session):
    campus = authenticate(db_session, "campus-ops", SAM, DEMO_PASSWORD)
    harbor = authenticate(db_session, "harbor-yard", SAM, DEMO_PASSWORD)
    assert campus is not None and harbor is not None
    assert campus.id != harbor.id  # two memberships ...
    assert campus.account_id == harbor.account_id  # ... of one person
    assert (campus.company.slug, campus.role_code) == ("campus-ops", "EMPLOYEE")
    assert (harbor.company.slug, harbor.role_code) == ("harbor-yard", "AREA_MANAGER")


def test_dual_employee_gets_the_membership_of_the_chosen_company(client, login_as, session_data, ui):
    login_as(SAM, company="harbor-yard")
    home = client.get("/").text
    assert "Harbor Yard" in home and ui("access.AREA_MANAGER") in home
    harbor_id = session_data()["user_id"]
    client.post("/logout")

    login_as(SAM, company="campus-ops")
    home = client.get("/").text
    assert "Campus Ops" in home and ui("access.EMPLOYEE") in home
    assert session_data()["user_id"] != harbor_id


def test_one_password_for_both_companies(db_session):
    account = db_session.scalar(select(Account).where(Account.email == SAM))
    account.credential.password_hash = hash_password("NewPersonal1!")
    db_session.commit()
    for slug in ("campus-ops", "harbor-yard"):
        assert authenticate(db_session, slug, SAM, "NewPersonal1!") is not None
        assert authenticate(db_session, slug, SAM, DEMO_PASSWORD) is None


def test_add_membership_for_existing_person(db_session, lookup):
    """Ada joins Harbor Yard: new membership linked to her existing account and password."""
    ada = lookup.user("E-1001")
    assert authenticate(db_session, "harbor-yard", ada.email, DEMO_PASSWORD) is None
    _add_user(
        db_session, company="harbor-yard", email=ada.email, emp="HY-E-4002", first="Ada", account=ada.account
    )
    joined = authenticate(db_session, "harbor-yard", ada.email, DEMO_PASSWORD)
    assert joined is not None and joined.account_id == ada.account_id and joined.id != ada.id


def test_one_membership_per_company_per_person(db_session, lookup):
    ada = lookup.user("E-1001")
    with pytest.raises(IntegrityError):
        _add_user(db_session, company="campus-ops", email="ada.second@facility.example", emp="E-9", first="A",
                  account=ada.account)
    db_session.rollback()


def test_membership_deactivation_is_per_company(db_session, lookup):
    lookup.user("HY-R-6001").active = False
    db_session.commit()
    assert authenticate(db_session, "harbor-yard", SAM, DEMO_PASSWORD) is None
    assert authenticate(db_session, "campus-ops", SAM, DEMO_PASSWORD) is not None


def test_different_people_may_share_an_email_across_companies(db_session):
    """Login emails are unique per company only; two different accounts can use one."""
    twin = _add_user(db_session, company="harbor-yard", email="ada.employee@facility.example", emp="HY-9",
                     first="Other", password="OtherPass1!")
    assert authenticate(db_session, "harbor-yard", "ada.employee@facility.example", "OtherPass1!").id == twin.id
    assert authenticate(db_session, "harbor-yard", "ada.employee@facility.example", DEMO_PASSWORD) is None
    assert authenticate(db_session, "campus-ops", "ada.employee@facility.example", "OtherPass1!") is None


def test_email_unique_within_a_company(db_session):
    with pytest.raises(IntegrityError):
        _add_user(db_session, company="campus-ops", email="ADA.employee@facility.example", emp="E-9", first="Dup")
    db_session.rollback()


def test_employee_number_unique_within_a_company(db_session):
    with pytest.raises(IntegrityError):
        _add_user(db_session, company="harbor-yard", email="new@harbor-yard.example", emp="HY-E-4001", first="Dup")
    db_session.rollback()


def test_email_is_stored_normalized(db_session):
    user = _add_user(db_session, company="harbor-yard", email="  Mixed.Case@Harbor-Yard.Example ", emp="HY-9", first="M")
    assert user.email == "mixed.case@harbor-yard.example"


# --- active flags -------------------------------------------------------------


def test_inactive_company_cannot_log_in_and_sessions_end(client, db_session, login_as, ui):
    login_as("hugo.employee@harbor-yard.example")
    assert client.get("/", follow_redirects=False).status_code == 200

    _company(db_session, "harbor-yard").active = False
    db_session.commit()

    assert client.get("/", follow_redirects=False).status_code == 303  # existing session ends
    login_page = client.get("/login").text
    assert '<option value="harbor-yard"' not in login_page
    response = client.post(
        "/login",
        data={"company": "harbor-yard", "login": "HY-E-4001", "password": DEMO_PASSWORD, "next": "/"},
    )
    assert ui("login.invalid") in response.text


def test_inactive_user_cannot_log_in(db_session, lookup):
    lookup.user("E-1002").active = False
    db_session.commit()
    assert authenticate(db_session, "campus-ops", "E-1002", DEMO_PASSWORD) is None


# --- technicians are scoped to the manager's company --------------------------


def test_account_email_is_normalized():
    assert Account(email="  Sam.Rivera@Mail.Example ").email == SAM


def test_manager_only_sees_own_company_technicians(client, login_as, lookup):
    login_as("morgan.manager@facility.example")
    body = client.get("/requests").text
    assert f'<option value="{lookup.user("T-2001").id}"' in body
    assert f'<option value="{lookup.user("HY-T-5001").id}"' not in body
    assert "Hana Technician" not in body


def test_manager_cannot_assign_other_company_technician(client, login_as, db_session, lookup, ui):
    login_as("morgan.manager@facility.example")
    pending = lookup.request("REQ-2026-0001")
    body = client.post(
        "/requests/assign",
        data={"requestId": pending.id, "technicianId": lookup.user("HY-T-5001").id},
        follow_redirects=True,
    ).text
    assert ui("error.userNotFound") in body  # indistinguishable from a missing user
    db_session.expire_all()
    assert db_session.get(ServiceRequest, pending.id).assigned_technician_id is None


def test_user_service_scoping(db_session, lookup):
    hana = lookup.user("HY-T-5001")
    campus = _company(db_session, "campus-ops")
    harbor = _company(db_session, "harbor-yard")
    assert user_service.require_by_id(db_session, hana.id).id == hana.id
    assert user_service.require_by_id(db_session, hana.id, company_id=harbor.id).id == hana.id
    assert [u.employee_number for u in user_service.list_technicians(db_session, company_id=harbor.id)] == ["HY-T-5001"]
    assert {u.employee_number for u in user_service.list_technicians(db_session, company_id=campus.id)} == {
        "T-2001",
        "T-2002",
    }
    with pytest.raises(Exception):
        user_service.require_by_id(db_session, hana.id, company_id=campus.id)


def test_harbor_yard_paths_work(client, login_as, db_session, lookup):
    """Harbor Yard employee and technician can sign in and use their pages."""
    login_as("hugo.employee@harbor-yard.example")
    assert client.get("/requests/new").status_code == 200
    client.post("/logout")
    login_as("HY-T-5001")
    page = client.get("/maintenance/new")
    assert page.status_code == 200
    assert db_session.scalar(select(func.count()).select_from(AppUser)) == len(DEMO_USERS)
