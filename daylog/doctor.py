"""Environment checks shared by `daylog doctor` and the web UI's
Settings > Run doctor button — one implementation, two presentations.
"""
from __future__ import annotations

import os
import platform
import shutil
import sys
from typing import Any, Dict, List

from . import storage
from .collectors import window as window_collector
from .config import ConfigError, config_path, load_config
from .paths import db_path


def _check(label: str, ok: bool, detail: str = "", optional: bool = False) -> Dict[str, Any]:
    return {"label": label, "ok": ok, "detail": detail, "optional": optional}


def _windows_checks() -> List[Dict[str, Any]]:
    try:
        import ctypes

        ctypes.windll.user32  # noqa: B018 — attribute access is the check
        return [_check("Windows window-tracking API (user32/kernel32 via ctypes)", True)]
    except Exception as exc:
        return [_check("Windows window-tracking API (user32/kernel32 via ctypes)", False, str(exc))]


def _linux_checks() -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []
    warning = window_collector.wayland_warning()
    if warning:
        results.append(_check("Display server", False, warning))
    else:
        session_type = os.environ.get("XDG_SESSION_TYPE", "")
        results.append(_check("Display server", True, session_type or "X11 (assumed)"))

    has_xdotool = shutil.which("xdotool") is not None
    has_xprop = shutil.which("xprop") is not None
    results.append(
        _check(
            "xdotool or xprop available (active window title)",
            has_xdotool or has_xprop,
            "xdotool found" if has_xdotool else ("xprop found" if has_xprop else "install one of: xdotool, xprop"),
        )
    )

    has_xprintidle = shutil.which("xprintidle") is not None
    results.append(
        _check(
            "xprintidle available (optional; falls back to X11 screensaver extension)",
            has_xprintidle,
            "" if has_xprintidle else "not found — will use the ctypes/X11 fallback instead",
            optional=True,
        )
    )
    return results


def run_checks() -> List[Dict[str, Any]]:
    results: List[Dict[str, Any]] = []

    results.append(_check("Python >= 3.9", sys.version_info >= (3, 9), f"found {platform.python_version()}"))

    git_path = shutil.which("git")
    results.append(_check("git executable on PATH", git_path is not None, git_path or "not found — install git"))

    system = platform.system()
    if system == "Windows":
        results += _windows_checks()
    elif system == "Linux":
        results += _linux_checks()
    else:
        results.append(
            _check(
                f"Platform {system!r} window-tracking",
                False,
                "unsupported — only Windows and Linux/X11 are implemented",
            )
        )

    try:
        load_config()
        results.append(_check("config.json valid", True, str(config_path())))
    except ConfigError as exc:
        results.append(_check("config.json valid", False, str(exc)))

    try:
        with storage.open_db() as conn:
            conn.execute("SELECT 1")
        results.append(_check("database writable", True, str(db_path())))
    except Exception as exc:
        results.append(_check("database writable", False, str(exc)))

    return results


def all_required_ok(results: List[Dict[str, Any]]) -> bool:
    return all(r["ok"] for r in results if not r["optional"])
