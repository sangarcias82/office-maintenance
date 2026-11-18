def test_manager_sees_report(client, login_as):
    login_as("morgan.manager@facility.example")
    response = client.get("/reports")
    assert response.status_code == 200
    assert b"REQ-2026-" in response.content
    assert b"statusChart" in response.content


def test_employee_forbidden_from_reports(client, login_as):
    login_as("ada.employee@facility.example")
    response = client.get("/reports", follow_redirects=False)
    assert response.status_code == 303


def test_lang_switch(client, login_as):
    login_as("morgan.manager@facility.example")
    response = client.get("/lang/es?next=/", follow_redirects=True)
    assert response.status_code == 200
    assert b"Inicio" in response.content or b"sistema" in response.content.lower()
