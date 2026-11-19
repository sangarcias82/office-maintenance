import pytest
from pydantic import ValidationError

from app import auth
from app.config import Settings, get_settings
from app.deps import safe_next_path

DEMO_PASSWORD = "OfficeDemo1!"


def _post_login(client, *, login, password=DEMO_PASSWORD, company="campus-ops", next="/", follow=True):
    data = {"login": login, "password": password, "next": next}
    if company is not None:
        data["company"] = company
    return client.post("/login", data=data, follow_redirects=follow)


# --- login page ---------------------------------------------------------------


def test_login_page_lists_active_companies(client, ui):
    body = client.get("/login").text
    assert ui("login.heading") in body
    assert ui("login.company") in body
    assert '<select name="company" required>' in body
    assert '<option value="campus-ops"' in body and "Campus Ops" in body
    assert '<option value="harbor-yard"' in body and "Harbor Yard" in body


def test_login_page_preselects_company(client):
    body = client.get("/login?company=harbor-yard").text
    assert '<option value="harbor-yard" selected>' in body


def test_login_page_in_spanish(client, set_lang, ui):
    set_lang("es")
    body = client.get("/login").text
    assert ui("login.heading", "es") in body
    assert ui("login.company", "es") in body


# --- successful login ---------------------------------------------------------


def test_login_email_ok(client, login_as, ui):
    login_as("ada.employee@facility.example")
    home = client.get("/")
    assert home.status_code == 200
    assert "Ada Employee" in home.text
    assert ui("home.heading") in home.text


def test_login_employee_number_ok(client, login_as):
    login_as("T-2001")
    assert "Tess Technician" in client.get("/").text


def test_login_email_is_case_insensitive(client, login_as):
    login_as("  ADA.Employee@Facility.example ")
    assert client.get("/", follow_redirects=False).status_code == 200


# --- failed login: one message for every reason ------------------------------


@pytest.mark.parametrize(
    ("login", "password", "company"),
    [
        ("ada.employee@facility.example", "wrong", "campus-ops"),  # bad password
        ("nobody@example.com", DEMO_PASSWORD, "campus-ops"),  # unknown user
        ("ada.employee@facility.example", DEMO_PASSWORD, "harbor-yard"),  # other company
        ("E-1001", DEMO_PASSWORD, "harbor-yard"),  # employee number, other company
        ("ada.employee@facility.example", DEMO_PASSWORD, "no-such-co"),  # unknown company
        ("ada.employee@facility.example", DEMO_PASSWORD, ""),  # no company chosen
        ("ada.employee@facility.example", DEMO_PASSWORD, None),  # company field missing
        ("   ", DEMO_PASSWORD, "campus-ops"),  # blank login
    ],
)
def test_failed_login_uses_one_message(client, session_data, ui, login, password, company):
    response = _post_login(client, login=login, password=password, company=company)
    assert response.status_code == 200
    assert ui("login.invalid") in response.text
    assert "user_id" not in session_data()


def test_failed_login_keeps_form_values(client):
    body = _post_login(client, login="hugo.employee@harbor-yard.example", password="x", company="harbor-yard").text
    assert 'value="hugo.employee@harbor-yard.example"' in body
    assert '<option value="harbor-yard" selected>' in body


def test_login_error_is_translated(client, set_lang, ui):
    set_lang("es")
    assert ui("login.invalid", "es") in _post_login(client, login="nobody@example.com", password="x").text


@pytest.mark.parametrize("company", ["campus-ops", "harbor-yard", "no-such-co", ""])
def test_unknown_identity_still_runs_dummy_hash(client, monkeypatch, company):
    """Unknown user / wrong company do the same PBKDF2 work as a real check."""
    seen = []
    real_verify = auth.verify_password

    def spy(password, stored_hash):
        seen.append(stored_hash)
        return real_verify(password, stored_hash)

    monkeypatch.setattr(auth, "verify_password", spy)
    _post_login(client, login="ghost@example.com", company=company)
    assert seen == [auth.dummy_hash()]


def test_password_verification_unchanged():
    stored = auth.hash_password("s3cret!")
    assert stored.startswith(f"pbkdf2_sha256${get_settings().password_hash_iterations}$")
    assert auth.verify_password("s3cret!", stored)
    assert not auth.verify_password("nope", stored)
    assert not auth.verify_password("s3cret!", "md5$1$abc$def")
    assert not auth.verify_password("s3cret!", "pbkdf2_sha256$not-a-number$x$y")


def test_production_work_factor_is_600k():
    """Tests lower the cost via PASSWORD_HASH_ITERATIONS; the shipped default stays 600k."""
    assert Settings.model_fields["password_hash_iterations"].default == 600_000
    with pytest.raises(ValidationError):
        Settings(password_hash_iterations=10)


def test_hashes_verify_with_their_own_iteration_count():
    """A 600k hash still verifies while new hashes use another count (count is in the hash)."""
    strong = auth.hash_password("s3cret!", iterations=600_000)
    assert strong.startswith("pbkdf2_sha256$600000$")
    assert auth.verify_password("s3cret!", strong)


def test_dummy_hash_costs_the_same_as_real_hashes():
    assert auth.dummy_hash().split("$")[1] == str(get_settings().password_hash_iterations)


# --- session lifecycle --------------------------------------------------------


def test_login_rotates_session(client, forge_session, session_data, lookup):
    """Anything in the pre-login session (even another user id) is discarded."""
    forge_session({"user_id": 999_999, "_flash": [{"message": "stale", "category": "ok"}], "planted": "x"})
    _post_login(client, login="ada.employee@facility.example", follow=False)
    assert session_data() == {"user_id": lookup.user("E-1001").id}


def test_login_keeps_only_language_choice(client, set_lang, forge_session, session_data, lookup):
    forge_session({"lang": "es", "planted": "x"})
    _post_login(client, login="T-2001", follow=False)
    assert session_data() == {"lang": "es", "user_id": lookup.user("T-2001").id}


def test_logout_clears_whole_session(client, login_as, set_lang, session_data):
    login_as("ada.employee@facility.example")
    set_lang("es")
    assert session_data().get("lang") == "es"
    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/login"
    assert session_data() == {}
    assert client.get("/", follow_redirects=False).status_code == 303
    assert 'lang="en"' in client.get("/login").text  # language reset too


def test_protected_home_redirects(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


# --- redirects after login / language switch ---------------------------------

UNSAFE_NEXT = [
    "//evil.example/x",
    "https://evil.example/",
    "http:/evil.example",
    "/\\evil.example",
    "\\\\evil.example",
    "/\t/evil.example",
    "/\n/evil.example",
    "evil.example",
    "javascript:alert(1)",
]


@pytest.mark.parametrize("target", UNSAFE_NEXT)
def test_safe_next_path_rejects_offsite(target):
    assert safe_next_path(target) == "/"


@pytest.mark.parametrize("target", ["/", "/reports", "/requests?q=a%2Fb&statusId=2", "/maintenance/new?requestId=3"])
def test_safe_next_path_keeps_local_paths(target):
    assert safe_next_path(target) == target


def test_safe_next_path_default():
    assert safe_next_path("", default="/requests") == "/requests"
    assert safe_next_path(None) == "/"


@pytest.mark.parametrize("target", UNSAFE_NEXT)
def test_post_login_never_redirects_offsite(client, target):
    response = _post_login(client, login="R-3001", next=target, follow=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/"


@pytest.mark.parametrize("target", UNSAFE_NEXT)
def test_get_login_when_signed_in_never_redirects_offsite(client, login_as, target):
    login_as("R-3001")
    response = client.get("/login", params={"next": target}, follow_redirects=False)
    assert response.headers["location"] == "/"


@pytest.mark.parametrize("target", UNSAFE_NEXT)
def test_lang_switch_never_redirects_offsite(client, target):
    response = client.get("/lang/es", params={"next": target}, follow_redirects=False)
    assert response.headers["location"] == "/"


def test_login_page_does_not_echo_unsafe_next(client):
    body = client.get("/login", params={"next": "//evil.example"}).text
    assert 'name="next" value="/"' in body


def test_login_redirects_to_safe_next(client):
    response = _post_login(client, login="R-3001", next="/reports", follow=False)
    assert response.headers["location"] == "/reports"


def test_login_page_when_signed_in_redirects(client, login_as):
    login_as("ada.employee@facility.example")
    response = client.get("/login?next=/requests", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/requests"


def test_login_required_round_trip(client):
    first = client.get("/reports", follow_redirects=False)
    assert first.headers["location"] == "/login?next=%2Freports"
    response = _post_login(client, login="R-3001", next="/reports", follow=False)
    assert response.headers["location"] == "/reports"


# --- header / language --------------------------------------------------------


def test_header_access_chip_is_translated(client, login_as, set_lang, ui):
    login_as("tess.tech@facility.example")
    assert ui("access.TECHNICIAN") in client.get("/").text
    set_lang("es")
    assert ui("access.TECHNICIAN", "es") in client.get("/").text


def test_home_menus_follow_role(client, login_as, ui):
    login_as("morgan.manager@facility.example")
    body = client.get("/").text
    assert ui("home.manager.report") in body
    assert 'href="/reports"' in body
    assert 'href="/maintenance/new"' not in body


def test_lang_switch_rejects_unknown_language(client, login_as):
    login_as("ada.employee@facility.example")
    client.get("/lang/fr?next=/", follow_redirects=False)
    assert 'lang="en"' in client.get("/").text
