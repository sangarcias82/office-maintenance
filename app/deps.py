"""Shared template helpers and flash bag."""

from __future__ import annotations

from typing import Any, Optional
from urllib.parse import urlencode

from fastapi import Request
from fastapi.templating import Jinja2Templates
from starlette.responses import RedirectResponse

from app.auth import get_lang
from app.i18n import catalog_label, t
from app.models import AppUser
from pathlib import Path

TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))


def flash(request: Request, message: str, category: str = "ok") -> None:
    request.session.setdefault("_flash", []).append({"message": message, "category": category})


def pop_flashes(request: Request) -> list[dict[str, str]]:
    return request.session.pop("_flash", [])


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

    context = {
        "request": request,
        "current_user": current_user,
        "lang": lang,
        "current_path": request.url.path,
        "flashes": pop_flashes(request),
        "t": _,
        "label": label,
        "title": _(title_key),
        **ctx,
    }
    return templates.TemplateResponse(request, name, context)
