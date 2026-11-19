"""Keep the README in step with the code (settings, test files, modules, links)."""

from __future__ import annotations

import re
from pathlib import Path

from app.config import Settings

ROOT = Path(__file__).resolve().parent.parent
README = (ROOT / "README.md").read_text(encoding="utf-8")
ENV_EXAMPLE = (ROOT / ".env.example").read_text(encoding="utf-8")


def _section(title: str) -> str:
    start = README.index(f"## {title}")
    nxt = README.find("\n## ", start + 1)
    return README[start: nxt if nxt > 0 else None]


def test_every_setting_is_documented_and_in_env_example():
    config = _section("Configuration")
    for name in Settings.model_fields:
        env = name.upper()
        assert f"`{env}`" in config, f"{env} missing from README Configuration"
        assert env in ENV_EXAMPLE, f"{env} missing from .env.example"


def test_env_example_has_no_unknown_settings():
    known = {name.upper() for name in Settings.model_fields}
    for line in ENV_EXAMPLE.splitlines():
        match = re.match(r"#?\s*([A-Z_]+)=", line)
        if match:
            assert match.group(1) in known, match.group(1)


def test_every_test_file_is_listed():
    tests = _section("Tests")
    for path in (ROOT / "tests").glob("test_*.py"):
        assert f"tests/{path.name}" in tests, f"{path.name} missing from README Tests table"


def test_every_app_module_is_in_the_layout():
    layout = _section("Layout")
    for path in (ROOT / "app").glob("*.py"):
        if path.name != "__init__.py":
            assert path.name in layout, f"app/{path.name} missing from README Layout"


def test_contents_links_point_to_headings():
    anchors = {
        re.sub(r"[^\w\- ]", "", h.strip().lower()).replace(" ", "-")
        for h in re.findall(r"^#{2,3} (.+)$", README, flags=re.M)
    }
    links = re.findall(r"\]\(#([\w-]+)\)", README)
    assert len(links) >= 15  # the contents list alone has more
    for link in links:
        assert link in anchors, f"broken README link #{link}"
