"""Language files (app/locales/*.json) are complete and every key the app uses exists."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app import i18n
from app.exceptions import BusinessError
from app.i18n import LOCALES_DIR, MESSAGES, SUPPORTED_LANGS, catalog_label, error_text, load_messages, t

APP_DIR = Path(__file__).resolve().parent.parent / "app"
PLACEHOLDER = re.compile(r"{(\w+)}")

# Words that are spelled the same in English and Spanish.
SAME_IN_ES = {"list.ticket", "maintenance.materialName"}


def _sources(*suffixes: str) -> list[Path]:
    return [p for p in APP_DIR.rglob("*") if p.suffix in suffixes]


def _template_keys() -> set[str]:
    keys: set[str] = set()
    for path in _sources(".html"):
        keys |= set(re.findall(r"""\bt\(\s*['"]([\w.]+)['"]""", path.read_text(encoding="utf-8")))
    return keys


def _python_keys() -> set[str]:
    """Message keys passed to t(), BusinessError(...), or title_key= in app code."""
    keys: set[str] = set()
    for path in _sources(".py"):
        text = path.read_text(encoding="utf-8")
        keys |= set(re.findall(r"""\bt\(\s*[\w()]+\s*,\s*['"]([\w.]+)['"]""", text))
        keys |= set(re.findall(r"""title_key\s*[=:]\s*(?:str\s*=\s*)?['"]([\w.]+)['"]""", text))
        keys |= set(re.findall(r"""['"](error\.\w+)['"]""", text))
    return keys


def test_every_supported_language_has_a_file():
    for lang in SUPPORTED_LANGS:
        assert (LOCALES_DIR / f"{lang}.json").is_file()
        assert lang in MESSAGES


def test_languages_define_the_same_keys():
    en = set(MESSAGES["en"])
    for lang in SUPPORTED_LANGS:
        assert set(MESSAGES[lang]) == en, f"{lang} differs from en"


def test_no_empty_translations():
    for lang in SUPPORTED_LANGS:
        empty = [k for k, v in MESSAGES[lang].items() if not str(v).strip()]
        assert not empty, f"{lang} has empty values: {empty}"


def test_placeholders_match_across_languages():
    for key, text in MESSAGES["en"].items():
        for lang in SUPPORTED_LANGS:
            assert set(PLACEHOLDER.findall(MESSAGES[lang][key])) == set(PLACEHOLDER.findall(text)), key


def test_spanish_is_actually_translated():
    untranslated = {k for k, v in MESSAGES["en"].items() if MESSAGES["es"][k] == v} - SAME_IN_ES
    assert not untranslated


def test_files_are_flat_string_maps():
    for lang in SUPPORTED_LANGS:
        raw = json.loads((LOCALES_DIR / f"{lang}.json").read_text(encoding="utf-8"))
        assert all(isinstance(k, str) and isinstance(v, str) for k, v in raw.items())


def test_all_template_keys_exist():
    missing = sorted(k for k in _template_keys() if k not in MESSAGES["en"])
    assert not missing


def test_all_python_keys_exist():
    keys = _python_keys()
    assert any(k.startswith("error.") for k in keys)  # sanity: scanner finds service errors
    missing = sorted(k for k in keys if k not in MESSAGES["en"])
    assert not missing


def test_seeded_catalog_codes_have_labels(db_session):
    from sqlalchemy import select

    from app.models import MaintenanceType, Priority, RequestStatus, Role

    for kind, model in (("role", Role), ("status", RequestStatus), ("priority", Priority), ("type", MaintenanceType)):
        for code in db_session.scalars(select(model.code)):
            for lang in SUPPORTED_LANGS:
                assert f"{kind}.{code}" in MESSAGES[lang], f"{lang}: {kind}.{code}"
    for code in db_session.scalars(select(Role.code)):
        assert f"access.{code}" in MESSAGES["en"]


def test_t_falls_back_to_english_then_key():
    assert t("fr", "nav.home") == MESSAGES["en"]["nav.home"]
    assert t("es", "does.not.exist") == "does.not.exist"


def test_t_formats_and_tolerates_bad_kwargs():
    assert "REQ-1" in t("en", "request.created", ticket="REQ-1")
    assert t("en", "nav.home", unused="x") == MESSAGES["en"]["nav.home"]
    assert t("en", "request.created", wrong="x") == MESSAGES["en"]["request.created"]


def test_catalog_label_uses_files_then_db_name():
    assert catalog_label("es", "status", "PENDING", "Pending") == MESSAGES["es"]["status.PENDING"]
    assert catalog_label("es", "status", "ON_HOLD", "On hold") == "On hold"


@pytest.mark.parametrize("lang", SUPPORTED_LANGS)
def test_error_text_translates_business_errors(lang):
    exc = BusinessError("Work performed is required.", "error.workRequired")
    assert error_text(lang, exc) == MESSAGES[lang]["error.workRequired"]


def test_error_text_falls_back_to_message():
    assert error_text("es", BusinessError("Plain message")) == "Plain message"
    assert error_text("es", BusinessError("Plain message", "error.unknownKey")) == "Plain message"


def test_load_messages_reads_a_locales_dir(tmp_path):
    for lang in SUPPORTED_LANGS:
        (tmp_path / f"{lang}.json").write_text(json.dumps({"k": lang}), encoding="utf-8")
    loaded = load_messages(tmp_path)
    assert loaded == {lang: {"k": lang} for lang in SUPPORTED_LANGS}
    assert i18n.MESSAGES is MESSAGES  # module-level table untouched


@pytest.mark.parametrize(
    ("login", "path"),
    [
        ("morgan.manager@facility.example", "/"),
        ("morgan.manager@facility.example", "/requests"),
        ("morgan.manager@facility.example", "/requests/new"),
        ("morgan.manager@facility.example", "/reports"),
        ("tess.tech@facility.example", "/maintenance/new"),
    ],
)
def test_pages_render_fully_in_spanish(client, login_as, set_lang, login, path):
    login_as(login)
    set_lang("es")
    body = client.get(path).text
    assert 'lang="es"' in body
    assert t("es", "nav.logout") in body
    assert t("en", "nav.logout") not in body
    assert t("en", "brand.title") not in body
