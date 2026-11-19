"""Generic company theme tokens (design tokens -> CSS custom properties).

A company may store ``theme_tokens`` (JSON) with values for a fixed set of
generic token names. They are emitted as ``--<name>: <value>`` on ``:root`` and
override the defaults in static/css/app.css. Nothing here is brand-specific:
the names describe roles in the UI (accent, background, ...), not a company.

Only hex colours are accepted, both when saving and when rendering, so a
token value can never break out of the CSS declaration.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

# token name -> what it controls (all are CSS variables defined in app.css)
THEME_TOKENS: dict[str, str] = {
    "accent": "buttons, active nav, links, company name, first chart colour",
    "accent-ink": "text on accent backgrounds",
    "accent-2": "secondary highlight (e.g. a brand stripe)",
    "bg": "page background (top)",
    "bg-deep": "page background (bottom)",
    "bg-glow": "soft glow in the top-left corner (hex with alpha)",
    "panel": "cards and panels",
    "ink": "main text",
    "muted": "secondary text",
    "line": "borders",
}

HEX_COLOUR = re.compile(r"^#(?:[0-9a-fA-F]{3,4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")


def validate_theme_tokens(tokens: Any) -> dict[str, str]:
    """Return a clean copy or raise ValueError (unknown name, non-hex value, wrong shape)."""
    if tokens is None:
        return {}
    if not isinstance(tokens, Mapping):
        raise ValueError("Theme tokens must be an object of token name -> hex colour.")
    clean: dict[str, str] = {}
    for name, value in tokens.items():
        if name not in THEME_TOKENS:
            raise ValueError(f"Unknown theme token: {name!r}.")
        if not isinstance(value, str) or not HEX_COLOUR.match(value):
            raise ValueError(f"Theme token {name!r} must be a hex colour like #1f5f8b.")
        clean[name] = value.lower()
    return clean


def css_variables(tokens: Any) -> list[tuple[str, str]]:
    """Safe (name, value) pairs for rendering; silently drops anything invalid.

    Re-checked at render time so a value written around the ORM validator
    (raw SQL, old data) still cannot inject CSS.
    """
    if not isinstance(tokens, Mapping):
        return []
    return sorted(
        (name, value.lower())
        for name, value in tokens.items()
        if name in THEME_TOKENS and isinstance(value, str) and HEX_COLOUR.match(value)
    )
