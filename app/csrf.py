"""Per-session CSRF token (synchronizer token pattern) for the server-rendered forms.

- One random token per session, stored in the signed session cookie under
  ``_csrf``. ``render()`` exposes it to templates as ``csrf_token`` and every
  state-changing form posts it back in the hidden field ``csrf_token``.
- ``require_csrf`` is a FastAPI dependency put on every POST route. It runs
  before the route body, so on a missing/wrong token nothing is written; the
  request is answered with a flash message and a 303 redirect instead.
- Login and logout replace the whole session, so the token is rotated with it.
- SameSite=Lax on the session cookie stays as a second layer (see main.py).
"""

from __future__ import annotations

import hmac
import logging
import secrets
from typing import Optional
from urllib.parse import urlsplit

from fastapi import Form, Request

logger = logging.getLogger(__name__)

CSRF_SESSION_KEY = "_csrf"
CSRF_FIELD = "csrf_token"
_TOKEN_BYTES = 32


class CSRFError(Exception):
    """Missing or mismatched CSRF token; handled in main.py (flash + redirect)."""


def get_csrf_token(request: Request) -> str:
    """Token of the current session, created on first use."""
    token = request.session.get(CSRF_SESSION_KEY)
    if not isinstance(token, str) or not token:
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        request.session[CSRF_SESSION_KEY] = token
    return token


def tokens_match(sent: Optional[str], expected: Optional[str]) -> bool:
    if not sent or not expected:
        return False
    return hmac.compare_digest(sent.encode("utf-8"), expected.encode("utf-8"))


def require_csrf(request: Request, csrf_token: str = Form("")) -> None:
    """Dependency for POST routes: the form's token must equal the session's token."""
    if not tokens_match(csrf_token, request.session.get(CSRF_SESSION_KEY)):
        logger.warning("CSRF check failed for %s %s", request.method, request.url.path)
        raise CSRFError()


def redirect_target(request: Request) -> str:
    """Where to send the user after a rejected form: the same-site page they came from, else '/'."""
    from app.deps import safe_next_path

    referer = request.headers.get("referer", "")
    parts = urlsplit(referer)
    if not referer or parts.netloc != request.url.netloc:
        return "/"
    path = parts.path + (f"?{parts.query}" if parts.query else "")
    return safe_next_path(path)
