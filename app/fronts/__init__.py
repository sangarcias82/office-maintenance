"""Per-company fronts: template overrides resolved by company slug, then ``_default``.

Layout::

    app/fronts/
      _default/templates/...          full set of templates (always present)
      <company-slug>/templates/...    only the files a company overrides

``templates_for(slug)`` returns a Jinja environment whose loader searches
``<slug>/templates`` first and ``_default/templates`` second, file by file. So a
company can override one partial (e.g. ``partials/brand.html``) and inherit
everything else; ``{% extends "base.html" %}`` / ``{% include %}`` resolve the
same way. A company without a folder, or a file missing from its folder, simply
falls back to ``_default`` (never a 500).

The slug comes from the signed-in user's company (never from the URL). It is
re-validated here, so only ``a-z0-9-`` names can become a path; ``_default``
itself can never be a company slug (underscore is not allowed in slugs).
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Optional

from fastapi.templating import Jinja2Templates

from app.models import COMPANY_SLUG_MAX, COMPANY_SLUG_RE

FRONTS_DIR = Path(__file__).resolve().parent
DEFAULT_FRONT = "_default"


def is_valid_front_slug(slug: Optional[str]) -> bool:
    return bool(slug) and len(slug) <= COMPANY_SLUG_MAX and bool(COMPANY_SLUG_RE.match(slug))


def search_path(slug: Optional[str], root: Path = FRONTS_DIR) -> list[Path]:
    """Template folders for ``slug`` in lookup order (override first, then default)."""
    default = root / DEFAULT_FRONT / "templates"
    if not is_valid_front_slug(slug):
        return [default]
    # The override folder does not need to exist: Jinja skips missing folders.
    # New override files need an app restart (Jinja caches templates it already loaded).
    return [root / slug / "templates", default]


@lru_cache(maxsize=64)
def _templates(paths: tuple[str, ...]) -> Jinja2Templates:
    return Jinja2Templates(directory=list(paths))


def templates_for(slug: Optional[str], root: Path = FRONTS_DIR) -> Jinja2Templates:
    return _templates(tuple(str(p) for p in search_path(slug, root)))


def resolved_front(slug: Optional[str], template: str, root: Path = FRONTS_DIR) -> str:
    """Which front actually serves ``template`` for ``slug`` (diagnostics/tests)."""
    for folder in search_path(slug, root):
        if (folder / template).is_file():
            return folder.parent.name
    return DEFAULT_FRONT


THEME_TEMPLATE = "partials/theme.css"


def serving_front(templates: Jinja2Templates, template: str = THEME_TEMPLATE) -> str:
    """Name of the front whose file the given environment actually loads for ``template``.

    Asked from the loader itself (not re-computed), so it is always what rendered.
    ``<front>/templates/<template>`` -> ``<front>``.
    """
    env = templates.env
    _source, filename, _uptodate = env.loader.get_source(env, template)
    path = Path(filename)
    return path.parents[len(Path(template).parts)].name
