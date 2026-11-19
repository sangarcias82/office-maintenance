#!/usr/bin/env python3
"""End-to-end harness for the multi-company feature, run against a LIVE server over HTTP.

    python scripts/e2e_check.py --base http://127.0.0.1:8000

Standard library only. Each demo user is a separate "browser" (own cookie jar);
forms are submitted with the CSRF token scraped from the rendered page, exactly
like a real browser. Expected UI text comes from app/locales/en.json, escaped
the way Jinja escapes it, so no message is hard-coded.

It walks the whole feature: sign-in per company, header, fronts and theme,
list/report isolation, a full Harbor Yard ticket lifecycle (create, assign, log
work with photo, close) and every refusal (cross-company ids, CSRF, open
redirect, anonymous evidence, bad dates, bogus catalog ids, oversized bodies).

It writes a few tickets/work logs tagged ``e2e-<run id>``. ``./for_testing --e2e``
runs it against a throwaway server and database; against your own server it
leaves those rows behind. Exit status 0 = all checks passed.
"""

from __future__ import annotations

import argparse
import http.client
import http.cookiejar
import json
import os
import re
import struct
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zlib
from dataclasses import dataclass, field
from email.message import Message
from pathlib import Path
from typing import Callable, Optional

APP_DIR = Path(__file__).resolve().parent.parent / "app"
MESSAGES: dict[str, str] = json.loads((APP_DIR / "locales" / "en.json").read_text(encoding="utf-8"))
DEMO_PASSWORD = os.environ.get("DEMO_PASSWORD", "OfficeDemo1!")
MAX_REQUEST_BYTES = int(os.environ.get("MAX_REQUEST_BYTES", 6 * 1024 * 1024))

# Seeded sample data (not UI text): a few words from each company's tickets/locations.
CAMPUS_MARKERS = ["Server-room AC is not cooling", "visitor restroom sink", "Server room"]
HARBOR_MARKERS = ["barrier arm is stuck", "reefer sockets trip", "North gate", "Quay crane 2"]


# --- text helpers -----------------------------------------------------------------


def escape(text: str) -> str:
    """Same escaping as Jinja/markupsafe (&, <, >, ", ')."""
    return (
        text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        .replace('"', "&#34;").replace("'", "&#39;")
    )


def msg(key: str, **params: str) -> str:
    text = MESSAGES[key]
    return escape(text.format(**params) if params else text)


def png_bytes(width: int = 16, height: int = 12) -> bytes:
    rows = b"".join(b"\x00" + bytes((30, 90, 140)) * width for _ in range(height))

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(rows)) + chunk(b"IEND", b"")


def multipart(fields: dict, files: dict) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts: list[bytes] = []
    for name, value in fields.items():
        for item in value if isinstance(value, list) else [value]:
            parts.append(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{item}\r\n'.encode()
            )
    for name, (filename, content, content_type) in files.items():
        parts.append(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"; filename="{filename}"\r\n'
                f"Content-Type: {content_type}\r\n\r\n"
            ).encode()
            + content
            + b"\r\n"
        )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


# --- a tiny browser ---------------------------------------------------------------


@dataclass
class Response:
    status: int
    headers: Message
    body: bytes

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")

    @property
    def location(self) -> str:
        value = self.headers.get("location", "")
        return urllib.parse.urlsplit(value)._replace(scheme="", netloc="").geturl() if value else ""


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):  # noqa: ANN002, ANN003
        return None


class Browser:
    """One user agent with its own cookies. Redirects are followed only on GET."""

    def __init__(self, base: str, name: str = "anonymous") -> None:
        self.base = base.rstrip("/")
        self.name = name
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar), _NoRedirect)

    def request(self, method: str, path: str, *, data=None, files=None, headers=None, raw: Optional[bytes] = None) -> Response:
        hdrs = dict(headers or {})
        body = raw
        if files:
            body, hdrs["Content-Type"] = multipart(data or {}, files)
        elif data is not None:
            body = urllib.parse.urlencode(data, doseq=True).encode()
            hdrs["Content-Type"] = "application/x-www-form-urlencoded"
        req = urllib.request.Request(self.base + path, data=body, method=method, headers=hdrs)
        try:
            resp = self.opener.open(req, timeout=30)
        except urllib.error.HTTPError as err:
            resp = err
        return Response(getattr(resp, "status", None) or resp.code, resp.headers, resp.read())

    def get(self, path: str, follow: bool = True) -> Response:
        response = self.request("GET", path)
        hops = 0
        while follow and response.status in (301, 302, 303, 307) and hops < 5:
            response = self.request("GET", response.location)
            hops += 1
        return response

    def csrf(self) -> str:
        match = re.search(r'name="csrf_token" value="([^"]+)"', self.get("/login").text)
        assert match, f"{self.name}: no CSRF token on the page"
        return match.group(1)

    def post(self, path: str, data: Optional[dict] = None, *, files=None, csrf: bool = True, headers=None) -> Response:
        fields = dict(data or {})
        if csrf:
            fields.setdefault("csrf_token", self.csrf())
        return self.request("POST", path, data=fields, files=files, headers=headers)

    def login(self, company: str, login: str, password: str = DEMO_PASSWORD, next_path: str = "/") -> Response:
        return self.post("/login", {"company": company, "login": login, "password": password, "next": next_path})

    def cookie(self, name: str = "maintenance_session") -> Optional[str]:
        return next((c.value for c in self.jar if c.name == name), None)


# --- page parsing -------------------------------------------------------------------


def options(html: str, select_name: str) -> dict[str, str]:
    """Visible text -> value of the <option>s inside <select name=...>."""
    block = re.search(rf'<select name="{select_name}".*?</select>', html, flags=re.S)
    assert block, f"select {select_name!r} not found"
    found = {}
    for value, text in re.findall(r'<option value="([^"]*)"[^>]*>(.*?)</option>', block.group(0), flags=re.S):
        if value:
            found[" ".join(text.split())] = value
    return found


def option_value(html: str, select_name: str, text_prefix: str) -> str:
    for text, value in options(html, select_name).items():
        if text.startswith(text_prefix):
            return value
    raise AssertionError(f"no option starting with {text_prefix!r} in {select_name!r}")


def flash(html: str) -> list[str]:
    return re.findall(r'<div class="flash flash-\w+">(.*?)</div>', html, flags=re.S)


def session_card(html: str) -> str:
    start = html.find('class="session-card"')
    assert start >= 0, "no session card (not signed in?)"
    return html[start:start + 1500]


# --- checks -------------------------------------------------------------------------


@dataclass
class Context:
    base: str
    run: str = field(default_factory=lambda: f"e2e-{uuid.uuid4().hex[:8]}")
    users: dict[str, Browser] = field(default_factory=dict)
    ticket_id: str = ""
    ticket_number: str = ""
    photo_id: str = ""
    photo: bytes = field(default_factory=png_bytes)

    def browser(self, name: str = "anonymous") -> Browser:
        return Browser(self.base, name)

    def user(self, key: str) -> Browser:
        return self.users[key]


CHECKS: list[tuple[str, Callable[[Context], None]]] = []


def check(name: str):
    def register(fn):
        CHECKS.append((name, fn))
        return fn

    return register


USERS = {
    # key: (company, login)
    "morgan": ("campus-ops", "morgan.manager@facility.example"),
    "tess": ("campus-ops", "T-2001"),
    "ada": ("campus-ops", "ada.employee@facility.example"),
    "sam_campus": ("campus-ops", "sam.rivera@mail.example"),
    "sam_harbor": ("harbor-yard", "sam.rivera@mail.example"),
    "hugo": ("harbor-yard", "HY-E-4001"),
    "hana": ("harbor-yard", "hana.tech@harbor-yard.example"),
}


@check("health endpoint answers")
def _health(ctx: Context) -> None:
    response = ctx.browser().get("/api/health")
    assert response.status == 200 and json.loads(response.body) == {"status": "ok"}


@check("login page offers both companies, default front, no tokens")
def _login_page(ctx: Context) -> None:
    html = ctx.browser().get("/login").text
    assert '<option value="campus-ops"' in html and '<option value="harbor-yard"' in html
    assert 'data-front="_default"' in html and 'id="company-tokens"' not in html


@check("every demo user signs in to their company")
def _sign_in(ctx: Context) -> None:
    for key, (company, login) in USERS.items():
        browser = ctx.browser(key)
        response = browser.login(company, login)
        assert response.status == 303 and response.location == "/", f"{key}: {response.status} {response.location}"
        ctx.users[key] = browser


@check("same credentials in the wrong company are refused")
def _wrong_company(ctx: Context) -> None:
    browser = ctx.browser()
    response = browser.login("harbor-yard", "ada.employee@facility.example")
    assert response.status == 200 and msg("login.invalid") in response.text


@check("header shows the company above the name; one person, role per company")
def _header(ctx: Context) -> None:
    harbor = session_card(ctx.user("sam_harbor").get("/").text)
    campus = session_card(ctx.user("sam_campus").get("/").text)
    assert harbor.index("Harbor Yard") < harbor.index("Sam Rivera") and msg("access.AREA_MANAGER") in harbor
    assert campus.index("Campus Ops") < campus.index("Sam Rivera") and msg("access.EMPLOYEE") in campus


def _front_theme(html: str) -> str:
    match = re.search(r'<style id="front-theme">(.*?)</style>', html, flags=re.S)
    assert match, "no front theme block"
    return match.group(1)


@check("each company has its own stable look hook (front theme, accent, background)")
def _looks(ctx: Context) -> None:
    harbor = ctx.user("sam_harbor").get("/").text
    campus = ctx.user("morgan").get("/").text
    assert '<body class="theme-harbor-yard" data-company="harbor-yard" data-theme="harbor-yard">' in harbor
    assert '<body class="theme-_default" data-company="campus-ops" data-theme="_default">' in campus
    harbor_css, campus_css = _front_theme(harbor), _front_theme(campus)
    assert re.search(r"--accent:\s*#1f5f8b;", harbor_css) and re.search(r"--bg:\s*#e9eff4;", harbor_css)
    assert "--accent" not in campus_css and "--bg" not in campus_css  # campus keeps the app.css defaults
    assert 'data-front="harbor-yard"' in harbor and "brand-harbor" in harbor
    assert 'data-front="_default"' in campus


@check("request lists are company-scoped")
def _list_isolation(ctx: Context) -> None:
    campus = ctx.user("morgan").get("/requests").text
    harbor = ctx.user("sam_harbor").get("/requests").text
    assert not [m for m in HARBOR_MARKERS[:2] if m in campus], "harbor tickets in campus list"
    assert not [m for m in CAMPUS_MARKERS[:2] if m in harbor], "campus tickets in harbor list"
    assert all(m in campus for m in CAMPUS_MARKERS[:2]) and all(m in harbor for m in HARBOR_MARKERS[:2])


@check("reports count only the manager's company")
def _report_isolation(ctx: Context) -> None:
    for key, others in (("morgan", HARBOR_MARKERS), ("sam_harbor", CAMPUS_MARKERS)):
        html = ctx.user(key).get("/reports").text
        total = int(re.search(r'<div class="value">(\d+)</div>', html).group(1))
        rows = len(re.findall(r"<tr>\s*<td>REQ-", html))
        assert total == rows > 0, f"{key}: total {total} vs rows {rows}"
        assert not [m for m in others if m in html], f"{key}: other company's data in report"


@check("Harbor Yard employee creates a ticket at a Harbor location")
def _create(ctx: Context) -> None:
    hugo = ctx.user("hugo")
    form = hugo.get("/requests/new").text
    assert "Admin floor 1" not in form, "campus locations offered to harbor employee"
    data = {
        "location_id": option_value(form, "location_id", "Warehouse A"),
        "priority_id": option_value(form, "priority_id", "High"),
        "maintenance_type_id": option_value(form, "maintenance_type_id", "Corrective"),
        "problem_description": f"{ctx.run} dock door 4 does not close",
    }
    response = hugo.post("/requests", data)
    assert response.status == 303 and response.location == "/requests", response.status
    listing = hugo.get("/requests").text
    ticket = re.search(r"Request (REQ-\d{4}-\d{4,}) registered\.", " ".join(flash(listing)))
    assert ticket, flash(listing)
    ctx.ticket_number = ticket.group(1)
    ctx.ticket_id = re.search(rf'href="/requests/(\d+)">{ctx.ticket_number}</a>', listing).group(1)


@check("create refuses other-company and bogus catalog ids without a 500")
def _create_refusals(ctx: Context) -> None:
    hugo = ctx.user("hugo")
    campus_location = option_value(ctx.user("morgan").get("/requests/new").text, "location_id", "Admin floor 1")
    form = hugo.get("/requests/new").text
    base = {
        "location_id": option_value(form, "location_id", "Warehouse A"),
        "priority_id": option_value(form, "priority_id", "High"),
        "maintenance_type_id": option_value(form, "maintenance_type_id", "Corrective"),
        "problem_description": f"{ctx.run} refused",
    }
    for override, key in (
        ({"location_id": campus_location}, "error.locationInvalid"),
        ({"location_id": "abc"}, "error.locationInvalid"),
        ({"priority_id": "999999"}, "error.priorityInvalid"),
        ({"maintenance_type_id": "999999"}, "error.typeInvalid"),
    ):
        response = hugo.post("/requests", {**base, **override})
        assert response.status == 200 and msg(key) in response.text, (override, response.status)
    assert f"{ctx.run} refused" not in hugo.get("/requests").text


@check("the new ticket is invisible to the other company")
def _new_ticket_isolation(ctx: Context) -> None:
    assert ctx.run not in ctx.user("morgan").get("/requests").text
    assert ctx.run in ctx.user("sam_harbor").get("/requests").text
    response = ctx.user("morgan").get(f"/requests/{ctx.ticket_id}", follow=False)
    assert response.status == 303 and response.location == "/requests"


@check("Campus manager cannot assign on the Harbor ticket")
def _cross_assign(ctx: Context) -> None:
    morgan = ctx.user("morgan")
    tess_id = option_value(morgan.get("/requests").text, "technicianId", "Tess Technician")
    response = morgan.post("/requests/assign", {"requestId": ctx.ticket_id, "technicianId": tess_id})
    assert response.status == 303
    assert msg("error.requestNotFound") in " ".join(flash(morgan.get("/requests").text))
    detail = ctx.user("sam_harbor").get(f"/requests/{ctx.ticket_id}").text
    assert msg("unassigned") in detail


@check("Harbor manager assigns the ticket to the Harbor technician")
def _assign(ctx: Context) -> None:
    sam = ctx.user("sam_harbor")
    listing = sam.get("/requests").text
    assert "Tess Technician" not in listing, "campus technician offered to harbor manager"
    hana_id = option_value(listing, "technicianId", "Hana Technician")
    response = sam.post("/requests/assign", {"requestId": ctx.ticket_id, "technicianId": hana_id})
    assert response.status == 303 and msg("list.assigned") in " ".join(flash(sam.get("/requests").text))


def _log(browser: Browser, request_id: str, status_id: str, extra=None, files=None) -> Response:
    data = {"request_id": request_id, "work_performed": "e2e work", "service_status_id": status_id, **(extra or {})}
    return browser.post("/maintenance", data, files=files)


def _status_ids(ctx: Context) -> dict[str, str]:
    return options(ctx.user("morgan").get("/requests").text, "statusId")  # all statuses, e.g. "Pending" -> "1"


@check("Campus technician cannot log work on the Harbor ticket")
def _cross_log(ctx: Context) -> None:
    statuses = _status_ids(ctx)
    response = _log(ctx.user("tess"), ctx.ticket_id, statuses["In progress"],
                    files={"evidenceFile": ("x.png", ctx.photo, "image/png")})
    assert response.status == 200 and msg("error.requestNotFound") in response.text
    assert ctx.run not in response.text
    assert msg("detail.noRecords") in ctx.user("sam_harbor").get(f"/requests/{ctx.ticket_id}").text


@check("log-work rules refuse bad status, quantity and photo; nothing saved")
def _log_rules(ctx: Context) -> None:
    hana, statuses = ctx.user("hana"), _status_ids(ctx)
    cases = [
        (statuses["Pending"], {}, None, "error.statusNotAllowed", {}),
        (statuses["In progress"], {"material_name": "Tape", "material_quantity": "-2"}, None, "error.materialQuantity", {"name": "Tape"}),
        (statuses["In progress"], {"material_name": "", "material_quantity": "3"}, None, "error.materialName", {}),
        (statuses["In progress"], {}, {"evidenceFile": ("fake.png", b"not an image", "image/png")}, "error.evidenceType", {}),
        (statuses["In progress"], {}, {"evidenceFile": ("x.svg", b"<svg/>", "image/svg+xml")}, "error.evidenceType", {}),
    ]
    for status_id, extra, files, key, params in cases:
        response = _log(hana, ctx.ticket_id, status_id, extra, files)
        assert response.status == 200 and msg(key, **params) in response.text, key
    assert msg("detail.noRecords") in hana.get(f"/requests/{ctx.ticket_id}").text


@check("Harbor technician logs work with a photo")
def _log_ok(ctx: Context) -> None:
    hana, statuses = ctx.user("hana"), _status_ids(ctx)
    response = _log(
        hana, ctx.ticket_id, statuses["In progress"],
        {"material_name": "Door sensor", "material_quantity": "2", "material_unit": "ea", "evidence_description": ctx.run},
        {"evidenceFile": ("door.png", ctx.photo, "image/png")},
    )
    assert response.status == 303, response.status
    detail = hana.get(f"/requests/{ctx.ticket_id}").text
    assert "Door sensor" in detail
    ctx.photo_id = re.search(r'src="/evidence/(\d+)"', detail).group(1)


@check("evidence is served only to users who can see the ticket")
def _evidence(ctx: Context) -> None:
    path = f"/evidence/{ctx.photo_id}"
    for key in ("hana", "sam_harbor", "hugo"):
        response = ctx.user(key).get(path)
        assert response.status == 200 and response.body == ctx.photo, key
        assert response.headers["content-type"] == "image/png" and "no-store" in response.headers["cache-control"]
    for key in ("morgan", "tess", "sam_campus"):
        assert ctx.user(key).get(path).status == 404, key
    anonymous = ctx.browser().get(path, follow=False)
    assert anonymous.status == 303 and anonymous.location.startswith("/login?next=")
    assert ctx.browser().get("/uploads/evidence/", follow=False).status == 404


@check("closing the ticket stops further work logs")
def _close(ctx: Context) -> None:
    hana, statuses = ctx.user("hana"), _status_ids(ctx)
    assert _log(hana, ctx.ticket_id, statuses["Completed"]).status == 303
    response = _log(hana, ctx.ticket_id, statuses["In progress"])
    assert response.status == 200 and msg("error.requestClosed") in response.text


@check("CSRF: a form without a token writes nothing")
def _csrf(ctx: Context) -> None:
    hugo = ctx.user("hugo")
    form = hugo.get("/requests/new").text
    response = hugo.post(
        "/requests",
        {
            "location_id": option_value(form, "location_id", "Warehouse A"),
            "priority_id": option_value(form, "priority_id", "Low"),
            "maintenance_type_id": option_value(form, "maintenance_type_id", "Corrective"),
            "problem_description": f"{ctx.run} no token",
        },
        csrf=False,
    )
    assert response.status == 303
    listing = hugo.get("/requests").text
    assert msg("csrf.invalid") in " ".join(flash(listing)) and f"{ctx.run} no token" not in listing
    stranger = ctx.browser()
    assert stranger.post("/logout", csrf=False).status == 303


@check("open redirects are refused")
def _open_redirect(ctx: Context) -> None:
    browser = ctx.browser()
    response = browser.login("campus-ops", "R-3001", next_path="//evil.example/x")
    assert response.status == 303 and response.location == "/", response.location
    assert browser.get("/lang/es?next=https://evil.example/", follow=False).location == "/"


@check("bad report dates do not cause a 500")
def _bad_date(ctx: Context) -> None:
    for value in ("not-a-date", "2026-02-30"):
        response = ctx.user("morgan").get(f"/reports?fromDate={value}")
        assert response.status == 200 and msg("error.invalidDate") in response.text, value


@check("security headers and cookie flags")
def _headers(ctx: Context) -> None:
    response = ctx.user("morgan").get("/")
    assert "frame-ancestors 'none'" in response.headers["content-security-policy"]
    assert response.headers["x-frame-options"] == "DENY" and response.headers["cache-control"] == "no-store"
    login = ctx.browser().request("GET", "/login")
    cookie = login.headers.get("set-cookie", "").lower()
    assert "httponly" in cookie and "samesite=lax" in cookie


@check("oversized requests are refused with 413 before the body is read")
def _size(ctx: Context) -> None:
    # Announce a too-large body and send none: the server must answer 413 from the
    # Content-Length header alone (it never reads, so the client cannot push 6 MB first).
    parts = urllib.parse.urlsplit(ctx.base)
    conn = http.client.HTTPConnection(parts.hostname, parts.port or 80, timeout=10)
    try:
        conn.putrequest("POST", "/maintenance")
        conn.putheader("Content-Type", "application/x-www-form-urlencoded")
        conn.putheader("Content-Length", str(MAX_REQUEST_BYTES + 1))
        conn.endheaders()
        response = conn.getresponse()
        assert response.status == 413, response.status
    finally:
        conn.close()


@check("logout clears the session")
def _logout(ctx: Context) -> None:
    hugo = ctx.user("hugo")
    response = hugo.post("/logout")
    assert response.status == 303 and response.location == "/login"
    assert hugo.get("/", follow=False).status == 303


# --- runner ---------------------------------------------------------------------------


def wait_for(base: str, seconds: float) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen(base.rstrip("/") + "/api/health", timeout=2) as resp:
                if resp.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False


def run(base: str, out=sys.stdout) -> tuple[int, int]:
    ctx = Context(base=base)
    passed = failed = 0
    print(f"E2E against {base} (run marker {ctx.run})", file=out)
    for name, fn in CHECKS:
        try:
            fn(ctx)
        except Exception as exc:  # noqa: BLE001 - report every failure and continue
            failed += 1
            print(f"  FAIL  {name}: {type(exc).__name__}: {exc}", file=out)
        else:
            passed += 1
            print(f"  ok    {name}", file=out)
    print(f"E2E result: {passed} passed, {failed} failed", file=out)
    return passed, failed


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--base", default="http://127.0.0.1:8000", help="server URL (default %(default)s)")
    parser.add_argument("--wait", type=float, default=20.0, help="seconds to wait for /api/health")
    args = parser.parse_args(argv)
    if not wait_for(args.base, args.wait):
        print(f"E2E: server at {args.base} did not answer /api/health", file=sys.stderr)
        return 2
    _passed, failed = run(args.base)
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
