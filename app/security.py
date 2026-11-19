"""App-wide hardening: session secret, security headers, request size limit.

- ``resolve_session_secret``: the session cookie carries ``user_id`` and is only
  *signed*, so a publicly known secret (the .env.example placeholder) would let
  anyone forge a session for any user. A missing/placeholder secret is replaced
  by a random per-process secret (sessions then reset on restart) and logged.
- ``SecurityMiddleware`` (pure ASGI, so streamed file responses are covered too):
    * refuses request bodies larger than ``max_body_bytes`` with 413 before any
      route runs (the Content-Length header is checked);
    * adds security headers to every response: CSP, X-Frame-Options (clickjacking
      would defeat the CSRF token), nosniff, Referrer-Policy, Permissions-Policy,
      ``Cache-Control: no-store`` on HTML pages (they contain company data), and
      HSTS when the session cookie is HTTPS-only.
"""

from __future__ import annotations

import logging
import secrets

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

logger = logging.getLogger(__name__)

PLACEHOLDER_SECRET = "YOUR_SECRET_KEY_HERE"
MIN_SECRET_LENGTH = 32

CONTENT_SECURITY_POLICY = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",  # no inline scripts anywhere (tested)
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",  # theme <style> blocks
        "font-src 'self' https://fonts.gstatic.com",
        "img-src 'self' data:",
        "object-src 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)

SECURITY_HEADERS: dict[str, str] = {
    "Content-Security-Policy": CONTENT_SECURITY_POLICY,
    "X-Frame-Options": "DENY",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "same-origin",  # the CSRF redirect still gets a same-site Referer
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}
HSTS = "max-age=31536000; includeSubDomains"


def resolve_session_secret(configured: str | None) -> str:
    """The secret to sign sessions with; never the public placeholder."""
    value = (configured or "").strip()
    if not value or value == PLACEHOLDER_SECRET:
        logger.warning(
            "SECRET_KEY is missing or the placeholder; using a random per-process secret. "
            "Sessions will not survive a restart. Set SECRET_KEY in .env for real use."
        )
        return secrets.token_urlsafe(48)
    if len(value) < MIN_SECRET_LENGTH:
        logger.warning("SECRET_KEY is shorter than %s characters; use a long random value.", MIN_SECRET_LENGTH)
    return value


class SecurityMiddleware:
    def __init__(self, app: ASGIApp, *, max_body_bytes: int, hsts: bool = False) -> None:
        self.app = app
        self.max_body_bytes = max_body_bytes
        self.hsts = hsts

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        length = Headers(scope=scope).get("content-length")
        if length is not None:
            try:
                too_big = int(length) > self.max_body_bytes
                bad = int(length) < 0
            except ValueError:
                too_big, bad = False, True
            if bad or too_big:
                status, text = (400, "Bad request") if bad else (413, "Request too large")
                logger.warning("Refused %s %s: %s (Content-Length %s)", scope["method"], scope["path"], text, length)
                await PlainTextResponse(text, status_code=status)(scope, receive, self._with_headers(send))
                return

        await self.app(scope, receive, self._with_headers(send))

    def _with_headers(self, send: Send) -> Send:
        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if headers.get("content-type", "").startswith("text/html"):
                    headers.setdefault("Cache-Control", "no-store")
                if self.hsts:
                    headers.setdefault("Strict-Transport-Security", HSTS)
            await send(message)

        return send_with_headers
