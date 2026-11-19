"""EN/ES UI strings, loaded from language files in app/locales/<lang>.json.

Each file is a flat JSON object of message key -> text. Keys mirror the
original messages*.properties set. Every language file must define the same
keys (enforced by tests/test_i18n.py); English is the fallback.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from app.exceptions import BusinessError

LOCALES_DIR = Path(__file__).resolve().parent / "locales"
DEFAULT_LANG = "en"
SUPPORTED_LANGS: tuple[str, ...] = ("en", "es")


def load_messages(locales_dir: Path = LOCALES_DIR) -> dict[str, dict[str, str]]:
    messages: dict[str, dict[str, str]] = {}
    for lang in SUPPORTED_LANGS:
        with (locales_dir / f"{lang}.json").open(encoding="utf-8") as fh:
            messages[lang] = json.load(fh)
    return messages


MESSAGES: dict[str, dict[str, str]] = load_messages()


def has_key(key: str) -> bool:
    return any(key in table for table in MESSAGES.values())


def t(lang: str, key: str, **kwargs: str) -> str:
    table = MESSAGES.get(lang) or MESSAGES[DEFAULT_LANG]
    text = table.get(key) or MESSAGES[DEFAULT_LANG].get(key) or key
    if kwargs:
        try:
            return text.format(**kwargs)
        except (KeyError, ValueError):
            return text
    return text


def catalog_label(lang: str, kind: str, code: str, fallback: str) -> str:
    """Translated label for a catalog row (role/status/priority/type/access), else the DB name."""
    key = f"{kind}.{code}"
    return t(lang, key) if has_key(key) else fallback


def error_text(lang: str, exc: "BusinessError") -> str:
    """Translated BusinessError text; falls back to the exception's English message."""
    if exc.message_key and has_key(exc.message_key):
        return t(lang, exc.message_key, **getattr(exc, "params", {}))
    return exc.message
