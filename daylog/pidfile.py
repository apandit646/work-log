"""A small pidfile so `daylog status` (and later, the tray icon and API)
can tell whether `daylog track` is running. This is advisory, not a lock:
if the process is killed the pidfile is left behind, and tracker_status()
notices the pid is dead and cleans it up on next check.
"""
from __future__ import annotations

import os
import platform
from pathlib import Path
from typing import Optional, Tuple

from .paths import data_dir


def pidfile_path() -> Path:
    return data_dir() / "tracker.pid"


def write_pidfile() -> None:
    path = pidfile_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(str(os.getpid()), encoding="utf-8")


def remove_pidfile() -> None:
    try:
        pidfile_path().unlink()
    except FileNotFoundError:
        pass


def read_pidfile() -> Optional[int]:
    path = pidfile_path()
    if not path.exists():
        return None
    try:
        return int(path.read_text(encoding="utf-8").strip())
    except (ValueError, OSError):
        return None


def is_process_running(pid: int) -> bool:
    if platform.system() == "Windows":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = ctypes.windll.kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        ctypes.windll.kernel32.CloseHandle(handle)
        return True

    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists, just owned by someone else
    return True


def tracker_status() -> Tuple[bool, Optional[int]]:
    """Returns (is_running, pid). Clears a stale pidfile left by a crash."""
    pid = read_pidfile()
    if pid is None:
        return False, None
    if is_process_running(pid):
        return True, pid
    remove_pidfile()
    return False, None
