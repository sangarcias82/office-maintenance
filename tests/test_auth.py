def test_login_page_loads(client):
    response = client.get("/login")
    assert response.status_code == 200
    assert b"Sign in" in response.content or b"Iniciar" in response.content


def test_login_email_ok(client, login_as):
    login_as("ada.employee@facility.example")
    home = client.get("/")
    assert home.status_code == 200
    assert b"Ada" in home.content


def test_login_employee_number_ok(client, login_as):
    login_as("T-2001")
    home = client.get("/")
    assert home.status_code == 200
    assert b"Tess" in home.content


def test_login_bad_password_same_message(client):
    response = client.post(
        "/login",
        data={"login": "ada.employee@facility.example", "password": "wrong", "next": "/"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Invalid credentials" in response.content or b"Credenciales" in response.content


def test_login_unknown_user_same_message(client):
    response = client.post(
        "/login",
        data={"login": "nobody@example.com", "password": "OfficeDemo1!", "next": "/"},
        follow_redirects=True,
    )
    assert response.status_code == 200
    assert b"Invalid credentials" in response.content or b"Credenciales" in response.content


def test_protected_home_redirects(client):
    response = client.get("/", follow_redirects=False)
    assert response.status_code == 303
    assert "/login" in response.headers["location"]


def test_logout(client, login_as):
    login_as("ada.employee@facility.example")
    response = client.post("/logout", follow_redirects=False)
    assert response.status_code == 303
    again = client.get("/", follow_redirects=False)
    assert again.status_code == 303
