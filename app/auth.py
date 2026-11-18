"""Session auth helpers. Password hashes live in user_credential, not AppUser."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.db import get_db
from app.models import AppUser, UserCredential

SESSION_USER_KEY = "user_id"
SESSION_LANG_KEY = "lang"

_PBKDF2_ALGO = "pbkdf2_sha256"
_PBKDF2_ITERATIONS = 600_000
_SALT_BYTES = 16
_DKLEN = 32


class LoginRequired(Exception):
    def __init__(self, next_path: str = "/"):
        self.next_path = next_path


class Forbidden(Exception):
    def __init__(self, message: str = "Forbidden"):
        self.message = message


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, _PBKDF2_ITERATIONS, dklen=_DKLEN
    )
    return f"{_PBKDF2_ALGO}${_PBKDF2_ITERATIONS}${_b64(salt)}${_b64(digest)}"


def verify_password(password: str, stored_hash: str) -> bool:
    try:
        algo, *params = stored_hash.split("$")
        if algo != _PBKDF2_ALGO:
            return False
        iterations, salt_b64, digest_b64 = params
        expected = base64.b64decode(digest_b64)
        candidate = hashlib.pbkdf2_hmac(
            "sha256",
            password.encode("utf-8"),
            base64.b64decode(salt_b64),
            int(iterations),
            dklen=len(expected),
        )
    except (ValueError, TypeError, OverflowError):
        return False
    return hmac.compare_digest(candidate, expected)


_DUMMY_HASH = hash_password(secrets.token_hex(16))


def find_active_credentials_by_login(db: Session, login: str) -> Optional[UserCredential]:
    key = login.strip()
    if not key:
        return None
    cred = db.scalar(
        select(UserCredential)
        .join(AppUser, UserCredential.user_id == AppUser.id)
        .options(joinedload(UserCredential.user).joinedload(AppUser.role))
        .where(AppUser.active.is_(True))
        .where(AppUser.email == key.lower())
    )
    if cred is not None:
        return cred
    return db.scalar(
        select(UserCredential)
        .join(AppUser, UserCredential.user_id == AppUser.id)
        .options(joinedload(UserCredential.user).joinedload(AppUser.role))
        .where(AppUser.active.is_(True))
        .where(AppUser.employee_number == key)
    )


def authenticate(db: Session, login: str, password: str) -> Optional[AppUser]:
    """Return the view-facing user on success. Same failure for unknown/bad password."""
    if not login or not login.strip() or password is None or password == "":
        return None
    creds = find_active_credentials_by_login(db, login)
    if creds is None:
        verify_password(password, _DUMMY_HASH)
        return None
    if not verify_password(password, creds.password_hash):
        return None
    return creds.user


def get_user_by_id(db: Session, user_id: int) -> Optional[AppUser]:
    return db.scalar(
        select(AppUser)
        .options(joinedload(AppUser.role))
        .where(AppUser.id == user_id, AppUser.active.is_(True))
    )


def current_user_optional(request: Request, db: Session = Depends(get_db)) -> Optional[AppUser]:
    user_id = request.session.get(SESSION_USER_KEY)
    if not user_id:
        return None
    return get_user_by_id(db, int(user_id))


def require_user(request: Request, db: Session = Depends(get_db)) -> AppUser:
    user = current_user_optional(request, db)
    if user is None:
        raise LoginRequired(next_path=request.url.path)
    return user


def set_session_user(request: Request, user: AppUser) -> None:
    request.session[SESSION_USER_KEY] = user.id


def clear_session_user(request: Request) -> None:
    request.session.pop(SESSION_USER_KEY, None)


def get_lang(request: Request) -> str:
    lang = request.session.get(SESSION_LANG_KEY, "en")
    return lang if lang in ("en", "es") else "en"


def set_lang(request: Request, lang: str) -> None:
    request.session[SESSION_LANG_KEY] = lang if lang in ("en", "es") else "en"
