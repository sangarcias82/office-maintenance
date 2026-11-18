from __future__ import annotations

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.orm import Session

from app.auth import (
    authenticate,
    clear_session_user,
    current_user_optional,
    get_lang,
    set_lang,
    set_session_user,
)
from app.db import get_db
from app.deps import flash, render
from app.i18n import t

router = APIRouter(tags=["auth"])


@router.get("/login")
def login_form(
    request: Request,
    next: str = "/",
    db: Session = Depends(get_db),
):
    user = current_user_optional(request, db)
    if user is not None:
        return RedirectResponse(next or "/", status_code=303)
    return render(request, "login.html", current_user=None, title_key="login.title", next=next or "/")


@router.post("/login")
def login_submit(
    request: Request,
    login: str = Form(...),
    password: str = Form(...),
    next: str = Form("/"),
    db: Session = Depends(get_db),
):
    user = authenticate(db, login, password)
    if user is None:
        flash(request, t(get_lang(request), "login.invalid"), "error")
        return render(
            request,
            "login.html",
            current_user=None,
            title_key="login.title",
            next=next or "/",
            login_value=login,
        )
    set_session_user(request, user)
    target = next if next and next.startswith("/") else "/"
    return RedirectResponse(target, status_code=303)


@router.post("/logout")
def logout(request: Request):
    clear_session_user(request)
    return RedirectResponse("/login", status_code=303)


@router.get("/lang/{lang}")
def switch_lang(request: Request, lang: str, next: str = "/"):
    set_lang(request, lang)
    target = next if next and next.startswith("/") else "/"
    return RedirectResponse(target, status_code=303)
