"""Shared template helpers and flash bag."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import Request
from starlette.responses import RedirectResponse

from app.auth import get_lang
from app.csrf import get_csrf_token
from app.fronts import serving_front, templates_for
from app.i18n import catalog_label, t
from app.models import AppUser, PhotoEvidence


def flash(request: Request, message: str, category: str = "ok") -> None:
    request.session.setdefault("_flash", []).append({"message": message, "category": category})


def pop_flashes(request: Request) -> list[dict[str, str]]:
    return request.session.pop("_flash", [])


def safe_next_path(value: Optional[str], default: str = "/") -> str:
    r"""Return ``value`` only if it is a same-site path; otherwise ``default``.

    Blocks open redirects such as ``//evil.example``, ``/\evil.example``,
    ``https://evil.example`` and paths with control characters (browsers drop
    tabs/newlines, which can turn ``/<TAB>/evil`` into ``//evil``).
    """
    if not value:
        return default
    if not value.startswith("/") or value.startswith("//") or "\\" in value:
        return default
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in value):
        return default
    # A single leading "/" with no backslash or control characters cannot carry a
    # scheme or host, so the value is a same-site path.
    return value


def redirect_with_query(path: str, **params: Any) -> RedirectResponse:
    qs = urlencode({k: v for k, v in params.items() if v is not None and v != ""})
    url = f"{path}?{qs}" if qs else path
    return RedirectResponse(url, status_code=303)


def render(
    request: Request,
    name: str,
    *,
    current_user: Optional[AppUser] = None,
    title_key: str = "brand.title",
    **ctx: Any,
):
    lang = get_lang(request)

    def _(key: str, **kwargs: str) -> str:
        return t(lang, key, **kwargs)

    def label(kind: str, code: str, fallback: str) -> str:
        return catalog_label(lang, kind, code, fallback)

    def evidence_url(photo: PhotoEvidence) -> str:
        # Built at render time from the route; the DB only stores a relative path.
        return request.app.url_path_for("evidence_file", photo_id=photo.id)

    # Company front: <slug>/templates overrides first, then _default (app/fronts).
    front_slug = current_user.company.slug if current_user is not None else None
    templates = templates_for(front_slug)

    context = {
        "request": request,
        "current_user": current_user,
        "lang": lang,
        "current_path": request.url.path,
        "flashes": pop_flashes(request),
        "csrf_token": get_csrf_token(request),
        "t": _,
        "label": label,
        "evidence_url": evidence_url,
        "front_slug": front_slug,
        "theme_vars": current_user.company.css_variables() if current_user is not None else [],
        # Stable page hook: <body data-company=... data-theme=...> (see base.html).
        "company_slug": front_slug or "",
        "theme_front": serving_front(templates),
        "title": _(title_key),
        **ctx,
    }
    return templates.TemplateResponse(request, name, context)
