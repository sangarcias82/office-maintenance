"""Run the end-to-end harness (scripts/e2e_check.py) against a real uvicorn process.

This is the same harness ``./for_testing --e2e`` runs; having it in pytest means
every test run exercises the whole multi-company feature over real HTTP too.
"""

from __future__ import annotations

import importlib.util
import io
import os
import socket
import subprocess
import sys
from pathlib import Path

import pytest
from markupsafe import escape

APP_ROOT = Path(__file__).resolve().parent.parent


def _load_harness():
    spec = importlib.util.spec_from_file_location("e2e_check", APP_ROOT / "scripts" / "e2e_check.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered while loading
    spec.loader.exec_module(module)
    return module


harness = _load_harness()


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    """A real server process with its own throwaway database and upload folder."""
    data = tmp_path_factory.mktemp("e2e")
    (data / "uploads").mkdir()
    port = _free_port()
    env = {
        **os.environ,
        "DATABASE_URL": f"sqlite:///{data / 'e2e.db'}",
        "UPLOAD_DIR": str(data / "uploads"),
        "PASSWORD_HASH_ITERATIONS": "1000",
    }
    process = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=APP_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        assert harness.wait_for(base, 30), "server did not start"
        yield base
    finally:
        process.terminate()
        process.wait(timeout=10)


def test_harness_passes_against_a_live_server(live_server):
    out = io.StringIO()
    passed, failed = harness.run(live_server, out=out)
    report = out.getvalue()
    assert failed == 0, report
    assert passed == len(harness.CHECKS) >= 20, report


def test_harness_reports_unreachable_server():
    port = _free_port()  # nothing listens here
    assert harness.main(["--base", f"http://127.0.0.1:{port}", "--wait", "0.5"]) == 2


def test_harness_escapes_like_jinja():
    sample = 'Material "Tape" <b>&\' ok'
    assert harness.escape(sample) == str(escape(sample))


def test_harness_reads_messages_from_the_language_file():
    assert harness.msg("error.materialQuantity", name="Tape") == str(
        escape(harness.MESSAGES["error.materialQuantity"].format(name="Tape"))
    )


def test_option_parsing():
    html = '<select name="x" required><option value="">Pick</option><option value="7" >\n  Warehouse A (HY-WH-A)\n</option></select>'
    assert harness.options(html, "x") == {"Warehouse A (HY-WH-A)": "7"}
    assert harness.option_value(html, "x", "Warehouse") == "7"
    with pytest.raises(AssertionError):
        harness.option_value(html, "x", "Nope")
