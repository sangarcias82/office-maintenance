import html
import json
import re

import pytest

from app.exceptions import BusinessError
from app.i18n import t
from app.services import reports as report_service

ALL_TICKETS = {f"REQ-2026-000{n}" for n in range(1, 6)}


def _tickets(body: str) -> set[str]:
    return set(re.findall(r"REQ-2026-\d{4}", body))


def _chart_labels(body: str, canvas_id: str) -> list[str]:
    match = re.search(rf'id="{canvas_id}"\s+data-labels=\'([^\']*)\'', body)
    assert match, canvas_id
    return json.loads(html.unescape(match.group(1)))


def test_manager_sees_report(client, login_as, ui):
    login_as("morgan.manager@facility.example")
    response = client.get("/reports")
    assert response.status_code == 200
    body = response.text
    assert ui("report.heading") in body
    assert _tickets(body) == ALL_TICKETS
    assert 'id="statusChart"' in body and 'id="typeChart"' in body
    assert sorted(_chart_labels(body, "statusChart")) == sorted(
        t("en", f"status.{c}") for c in ("PENDING", "IN_PROGRESS", "COMPLETED")
    )


def test_report_filters(client, login_as, lookup):
    login_as("morgan.manager@facility.example")

    def tickets(**params) -> set[str]:
        return _tickets(client.get("/reports", params=params).text)

    assert tickets(fromDate="2026-10-22") == {"REQ-2026-0004", "REQ-2026-0005"}
    assert tickets(toDate="2026-10-19") == {"REQ-2026-0003"}
    assert tickets(fromDate="2026-10-20", toDate="2026-10-21") == {"REQ-2026-0001", "REQ-2026-0002"}
    assert tickets(maintenanceTypeId=lookup.type_id("PREVENTIVE")) == {"REQ-2026-0004"}
    assert tickets(locationId=lookup.location_id("OFC-A-3-IT")) == {"REQ-2026-0002"}


def test_report_empty_state(client, login_as, ui):
    login_as("morgan.manager@facility.example")
    body = client.get("/reports", params={"fromDate": "2030-01-01"}).text
    assert ui("report.empty") in body
    assert _tickets(body) == set()


def test_report_labels_follow_language(client, login_as, set_lang):
    login_as("morgan.manager@facility.example")
    set_lang("es")
    body = client.get("/reports").text
    status_labels = _chart_labels(body, "statusChart")
    type_labels = _chart_labels(body, "typeChart")
    assert t("es", "status.IN_PROGRESS") in status_labels
    assert t("en", "status.IN_PROGRESS") not in status_labels
    assert t("es", "type.CORRECTIVE") in type_labels
    assert t("es", "status.COMPLETED") in body  # detail-table badge
    assert t("en", "type.IMPROVEMENT") not in body


def test_employee_forbidden_from_reports(client, login_as, ui):
    login_as("ada.employee@facility.example")
    response = client.get("/reports", follow_redirects=False)
    assert response.status_code == 303
    assert ui("forbidden") in client.get("/").text


def test_report_service_requires_manager(db_session, lookup):
    with pytest.raises(BusinessError) as info:
        report_service.filtered_requests(db_session, actor=lookup.user("T-2001"))
    assert info.value.message_key == "error.onlyManagersReport"


def test_report_service_helpers(db_session, lookup):
    rows = report_service.filtered_requests(db_session, actor=lookup.user("R-3001"))
    indicators = report_service.build_indicators(rows)
    assert (indicators.total, indicators.pending, indicators.in_progress, indicators.completed) == (5, 2, 2, 1)
    assert indicators.cancelled == 0

    detail = {r.ticket_number: r for r in report_service.build_report_rows(rows, unassigned_label="-")}
    assert detail["REQ-2026-0001"].technician == "-"
    assert detail["REQ-2026-0001"].attended_at is None
    assert detail["REQ-2026-0003"].attended_at is not None
    assert detail["REQ-2026-0003"].status == "Completed"  # default label: DB catalog name

    assert report_service.count_all(db_session, company_id=lookup.company("campus-ops").id) == 5
    assert report_service.count_all(db_session, company_id=lookup.company("harbor-yard").id) == 3


def test_cancelled_requests_are_counted(db_session, lookup):
    lookup.request("REQ-2026-0005").status_id = lookup.status_id("CANCELLED")
    db_session.commit()
    rows = report_service.filtered_requests(db_session, actor=lookup.user("R-3001"))
    assert report_service.build_indicators(rows).cancelled == 1


def test_lang_switch(client, login_as, ui):
    login_as("morgan.manager@facility.example")
    response = client.get("/lang/es?next=/", follow_redirects=True)
    assert response.status_code == 200
    assert ui("nav.home", "es") in response.text
    assert ui("home.heading", "es") in response.text


# --- malformed date filters (regression: used to be HTTP 500) -----------------


@pytest.mark.parametrize("lang", ["en", "es"])
@pytest.mark.parametrize("params", [{"fromDate": "not-a-date"}, {"toDate": "2026-13-45"}, {"fromDate": "2026-10-22", "toDate": "x"}])
def test_invalid_date_filter_is_reported_not_500(client, login_as, set_lang, ui, lang, params):
    login_as("morgan.manager@facility.example")
    set_lang(lang)
    response = client.get("/reports", params=params)
    assert response.status_code == 200
    assert ui("error.invalidDate", lang) in response.text
    assert _tickets(response.text) == ALL_TICKETS  # date filter dropped, report still shown
    assert 'name="fromDate" value=""' in response.text


def test_invalid_date_keeps_other_filters(client, login_as, lookup):
    login_as("morgan.manager@facility.example")
    body = client.get(
        "/reports", params={"fromDate": "bogus", "maintenanceTypeId": lookup.type_id("PREVENTIVE")}
    ).text
    assert _tickets(body) == {"REQ-2026-0004"}


def test_parse_filter_date():
    from datetime import date

    assert report_service.parse_filter_date(" 2026-10-22 ") == date(2026, 10, 22)
    assert report_service.parse_filter_date("") is None
    assert report_service.parse_filter_date(None) is None
    with pytest.raises(BusinessError) as info:
        report_service.parse_filter_date("22/10/2026")
    assert info.value.message_key == "error.invalidDate"
