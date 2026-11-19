"""Session auth helpers. Password hashes live in account_credential (per person), not AppUser."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from functools import lru_cache
from typing import Optional

from fastapi import Depends, Request
from sqlalchemy import select
from sqlalchemy.orm import Session, joinedload

from app.config import get_settings
from app.db import get_db
from app.models import AccountCredential, AppUser, Company
from app.services import companies as company_service

SESSION_USER_KEY = "user_id"
SESSION_LANG_KEY = "lang"

_PBKDF2_ALGO = "pbkdf2_sha256"
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


def hash_password(password: str, iterations: Optional[int] = None) -> str:
    rounds = iterations or get_settings().password_hash_iterations
    salt = secrets.token_bytes(_SALT_BYTES)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, rounds, dklen=_DKLEN)
    return f"{_PBKDF2_ALGO}${rounds}${_b64(salt)}${_b64(digest)}"


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


@lru_cache(maxsize=4)
def _dummy_hash_for(iterations: int) -> str:
    return hash_password(secrets.token_hex(16), iterations)


def dummy_hash() -> str:
    """Hash verified when no user matches, at the same cost as real hashes."""
    return _dummy_hash_for(get_settings().password_hash_iterations)


def find_active_membership(db: Session, company_id: int, login: str) -> Optional[AppUser]:
    """Active membership of ``company_id`` by login email, else employee number.

    Lookups never leave the company: identifiers are only unique per company.
    """
    key = login.strip()
    if not key:
        return None
    base = (
        select(AppUser)
        .options(joinedload(AppUser.role), joinedload(AppUser.company))
        .where(AppUser.active.is_(True), AppUser.company_id == company_id)
    )
    user = db.scalar(base.where(AppUser.email == key.lower()))
    if user is not None:
        return user
    return db.scalar(base.where(AppUser.employee_number == key))


def _password_hash_of(db: Session, user: AppUser) -> Optional[str]:
    return db.scalar(
        select(AccountCredential.password_hash).where(AccountCredential.account_id == user.account_id)
    )


def authenticate(db: Session, company_slug: str, login: str, password: str) -> Optional[AppUser]:
    """Return the company membership on success.

    The password belongs to the person (Account), so one employee working for
    two companies signs in to either with the same email and password; the
    chosen company decides which membership (role, data) the session gets.

    Unknown/inactive company, unknown user, and bad password all fail the same way,
    and all run one PBKDF2 verification so timing does not reveal which one it was.
    """
    if not login or not login.strip() or password is None or password == "":
        return None
    company = company_service.get_active_by_slug(db, company_slug)
    user = find_active_membership(db, company.id, login) if company else None
    stored = _password_hash_of(db, user) if user else None
    if stored is None:
        verify_password(password, dummy_hash())
        return None
    if not verify_password(password, stored):
        return None
    return user


def get_user_by_id(db: Session, user_id: int) -> Optional[AppUser]:
    """Active user of an active company (deactivating a company ends its sessions)."""
    return db.scalar(
        select(AppUser)
        .join(Company, AppUser.company_id == Company.id)
        .options(joinedload(AppUser.role), joinedload(AppUser.company))
        .where(AppUser.id == user_id, AppUser.active.is_(True), Company.active.is_(True))
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
    """Start a fresh session for ``user``.

    Sessions are signed cookies (no server-side id to rotate), so rotation means
    dropping everything from before login and writing a new payload. Only the UI
    language picked on the login page is carried over.
    """
    lang = request.session.get(SESSION_LANG_KEY)
    request.session.clear()
    if lang in ("en", "es"):
        request.session[SESSION_LANG_KEY] = lang
    request.session[SESSION_USER_KEY] = user.id


def clear_session(request: Request) -> None:
    """Logout: drop the whole session (user, language, flashes, anything else)."""
    request.session.clear()


def get_lang(request: Request) -> str:
    lang = request.session.get(SESSION_LANG_KEY, "en")
    return lang if lang in ("en", "es") else "en"


def set_lang(request: Request, lang: str) -> None:
    request.session[SESSION_LANG_KEY] = lang if lang in ("en", "es") else "en"
