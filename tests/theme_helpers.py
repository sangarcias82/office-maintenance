"""Helpers shared by theme tests: resolve CSS variables the way the browser does."""

from __future__ import annotations

import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "app" / "static"


def root_vars(css: str) -> dict[str, str]:
    """``--name: value`` pairs declared in ``:root { ... }`` blocks."""
    found: dict[str, str] = {}
    for block in re.findall(r":root\s*\{(.*?)\}", css, flags=re.S):
        found.update({k: v.strip().lower() for k, v in re.findall(r"--([\w-]+)\s*:\s*([^;]+);", block)})
    return found


def effective_tokens(html: str) -> dict[str, str]:
    """app.css defaults, then the front's theme.css (<style id="front-theme">), then company tokens."""
    tokens = root_vars((STATIC / "css" / "app.css").read_text())
    for style_id in ("front-theme", "company-tokens"):
        match = re.search(rf'<style id="{style_id}">(.*?)</style>', html, flags=re.S)
        if match:
            tokens.update(root_vars(match.group(1)))
    return tokens


def body_attrs(html: str) -> dict[str, str]:
    tag = re.search(r"<body\b([^>]*)>", html).group(1)
    return dict(re.findall(r'([\w-]+)="([^"]*)"', tag))
