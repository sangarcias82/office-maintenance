from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    authenticate,
    clear_session,
    current_user_optional,
    get_lang,
    set_lang,
    set_session_user,
)
from app.csrf import require_csrf
from app.db import get_db
from app.deps import flash, render, safe_next_path
from app.i18n import t
from app.services import companies as company_service

router = APIRouter(tags=["auth"])


def _login_page(request: Request, db: Session, *, next_path: str, company: str = "", login_value: str = ""):
    return render(
        request,
        "login.html",
        current_user=None,
        title_key="login.title",
        next=next_path,
        companies=company_service.list_active(db),
        company_value=company,
        login_value=login_value,
    )


@router.get("/login")
def login_form(
    request: Request,
    next: str = "/",
    company: str = "",
    db: Session = Depends(get_db),
):
    next_path = safe_next_path(next)
    user = current_user_optional(request, db)
    if user is not None:
        return RedirectResponse(next_path, status_code=303)
    return _login_page(request, db, next_path=next_path, company=company)


@router.post("/login", dependencies=[Depends(require_csrf)])
def login_submit(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    company: str = Form(""),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    next_path = safe_next_path(next)
    user = authenticate(db, company, login, password)
    if user is None:
        flash(request, t(get_lang(request), "login.invalid"), "error")
        return _login_page(request, db, next_path=next_path, company=company, login_value=login)
    set_session_user(request, user)
    return RedirectResponse(next_path, status_code=303)


@router.post("/logout", dependencies=[Depends(require_csrf)])
def logout(request: Request):
    clear_session(request)
    return RedirectResponse("/login", status_code=303)


@router.get("/lang/{lang}")
def switch_lang(request: Request, lang: str, next: str = "/"):
    set_lang(request, lang)
    return RedirectResponse(safe_next_path(next), status_code=303)
