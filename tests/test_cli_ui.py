"""`daylog ui` starts a real uvicorn server, so it's tested via a real
subprocess (like the track-command tests) rather than in-process — it
blocks forever in uvicorn.run(), which would hang the test otherwise."""
import os
import subprocess
import sys
import urllib.request
from pathlib import Path

import pytest

from daylog.cli import main
from daylog.config import load_config, save_config

REPO_ROOT = Path(__file__).resolve().parents[1]


def _free_port():
    import socket

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture
def ui_server(daylog_home):
    main(["init"])
    cfg = load_config()
    cfg.server.port = _free_port()
    save_config(cfg)

    env = os.environ.copy()
    env["DAYLOG_HOME"] = str(daylog_home)
    proc = subprocess.Popen(
        [sys.executable, "-m", "daylog.cli", "ui", "--no-browser"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base_url = f"http://127.0.0.1:{cfg.server.port}"
    try:
        _wait_for_server(base_url)
        yield base_url
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait(timeout=5)


def _wait_for_server(base_url, timeout=10.0):
    import time

    deadline = time.time() + timeout
    last_error = None
    while time.time() < deadline:
        try:
            urllib.request.urlopen(f"{base_url}/api/status", timeout=1)
            return
        except Exception as exc:  # noqa: BLE001 - server just isn't up yet
            last_error = exc
            time.sleep(0.1)
    raise RuntimeError(f"daylog ui never came up: {last_error}")


def test_ui_serves_index_html(ui_server):
    with urllib.request.urlopen(f"{ui_server}/") as resp:
        assert resp.status == 200
        body = resp.read().decode("utf-8")
    assert "<title>daylog</title>" in body


def test_ui_serves_static_assets(ui_server):
    with urllib.request.urlopen(f"{ui_server}/app.js") as resp:
        assert resp.status == 200
        assert "text/javascript" in resp.headers.get("Content-Type", "") or "application/javascript" in resp.headers.get("Content-Type", "")

    with urllib.request.urlopen(f"{ui_server}/style.css") as resp:
        assert resp.status == 200
        assert "text/css" in resp.headers.get("Content-Type", "")


def test_ui_serves_the_api_alongside_static_files(ui_server):
    with urllib.request.urlopen(f"{ui_server}/api/status") as resp:
        assert resp.status == 200
        import json

        data = json.loads(resp.read())
        assert "tracker_running" in data
