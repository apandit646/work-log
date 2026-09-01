"""Sets up `daylog track` to run automatically in the background: a
systemd user unit on Linux, a Task Scheduler entry (running at logon,
via pythonw.exe so no console window appears) on Windows. Neither
requires admin/root — a systemd --user unit and a per-user scheduled
task both install without elevation.

`daylog install`/`daylog uninstall` are the CLI surface for this module;
everything here just returns (ok, message) rather than printing directly,
so the CLI (and tests) can present the result however they like.
"""
from __future__ import annotations

import platform
import subprocess
import sys
from pathlib import Path
from typing import Tuple

_SERVICE_NAME = "daylog-tracker"
_TASK_NAME = "daylogTracker"


def _systemd_unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / f"{_SERVICE_NAME}.service"


def _systemd_unit_contents() -> str:
    return (
        "[Unit]\n"
        "Description=daylog activity tracker\n"
        "\n"
        "[Service]\n"
        f"ExecStart={sys.executable} -m daylog.cli track\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        "\n"
        "[Install]\n"
        "WantedBy=default.target\n"
    )


def install_linux() -> Tuple[bool, str]:
    unit_path = _systemd_unit_path()
    unit_path.parent.mkdir(parents=True, exist_ok=True)
    unit_path.write_text(_systemd_unit_contents(), encoding="utf-8")
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], check=True, capture_output=True, text=True)
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", f"{_SERVICE_NAME}.service"],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError:
        return False, f"Wrote {unit_path}, but systemctl isn't available to enable it. Enable it manually."
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or "").strip() or str(exc)
        return False, f"Wrote {unit_path}, but 'systemctl --user enable' failed: {detail}"
    return True, f"Installed and started the systemd user service {_SERVICE_NAME}.service ({unit_path})."


def uninstall_linux() -> Tuple[bool, str]:
    unit_path = _systemd_unit_path()
    try:
        subprocess.run(
            ["systemctl", "--user", "disable", "--now", f"{_SERVICE_NAME}.service"],
            capture_output=True, text=True,
        )
    except FileNotFoundError:
        pass
    removed = unit_path.exists()
    if removed:
        unit_path.unlink()
    try:
        subprocess.run(["systemctl", "--user", "daemon-reload"], capture_output=True, text=True)
    except FileNotFoundError:
        pass
    if removed:
        return True, f"Removed the systemd user service {_SERVICE_NAME}.service ({unit_path})."
    return True, f"No {_SERVICE_NAME}.service unit file was present ({unit_path}); nothing to remove."


def _pythonw_path() -> str:
    """Prefer pythonw.exe (no console window) if it sits next to the
    interpreter we're running under; fall back to the regular interpreter
    otherwise (e.g. a venv that only ships python.exe)."""
    python = Path(sys.executable)
    pythonw = python.with_name("pythonw.exe")
    return str(pythonw) if pythonw.exists() else str(python)


def install_windows() -> Tuple[bool, str]:
    command = f'"{_pythonw_path()}" -m daylog.cli track'
    try:
        subprocess.run(
            ["schtasks", "/create", "/tn", _TASK_NAME, "/tr", command, "/sc", "onlogon", "/rl", "limited", "/f"],
            check=True, capture_output=True, text=True,
        )
    except FileNotFoundError:
        return False, "schtasks.exe was not found — is this really Windows?"
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip() or str(exc)
        return False, f"Could not create the scheduled task: {detail}"
    return True, f"Created scheduled task {_TASK_NAME!r} (runs daylog track at logon, no console window)."


def uninstall_windows() -> Tuple[bool, str]:
    try:
        result = subprocess.run(
            ["schtasks", "/delete", "/tn", _TASK_NAME, "/f"], capture_output=True, text=True
        )
    except FileNotFoundError:
        return False, "schtasks.exe was not found — is this really Windows?"
    if result.returncode != 0:
        return True, f"No scheduled task {_TASK_NAME!r} was present; nothing to remove."
    return True, f"Removed scheduled task {_TASK_NAME!r}."


def install() -> Tuple[bool, str]:
    system = platform.system()
    if system == "Linux":
        return install_linux()
    if system == "Windows":
        return install_windows()
    return False, f"Autostart isn't implemented for {system!r} yet — run 'daylog track' manually instead."


def uninstall() -> Tuple[bool, str]:
    system = platform.system()
    if system == "Linux":
        return uninstall_linux()
    if system == "Windows":
        return uninstall_windows()
    return False, f"Autostart isn't implemented for {system!r} yet."
