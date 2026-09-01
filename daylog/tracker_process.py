"""Starts/stops `daylog track` as a detached background process — used by
the web UI's Settings screen (Start/Stop tracker buttons). The CLI itself
just runs `daylog track` directly in the foreground; this module exists
because the web server can't block on that loop itself.
"""
from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
import time
from typing import Dict, Optional

from . import pidfile

# Popen handles for children *this process* started, keyed by pid. Needed
# because a POSIX child that's never wait()-ed on becomes a zombie once it
# exits — os.kill(pid, 0) (what pidfile.is_process_running() uses) reports
# a zombie as "alive" forever, so stop_tracker() would never see it as
# stopped without reaping it via Popen.wait(). Only covers children we
# started ourselves in this process's lifetime; see the fallback below.
_children: Dict[int, "subprocess.Popen[bytes]"] = {}


def start_tracker() -> Optional[int]:
    """Returns the tracker's pid, or None if one was already running (in
    which case that pid is returned instead — starting twice is a no-op,
    matching `daylog track`'s own single-instance guard)."""
    running, existing_pid = pidfile.tracker_status()
    if running:
        return existing_pid

    kwargs = {}
    if platform.system() == "Windows":
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["start_new_session"] = True

    proc = subprocess.Popen(
        [sys.executable, "-m", "daylog.cli", "track"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )
    _children[proc.pid] = proc
    return proc.pid


def stop_tracker(timeout: float = 3.0) -> bool:
    """Signals the tracker to stop gracefully (same shutdown path as
    Ctrl+C) and waits up to `timeout` seconds for it to exit. Returns
    True if it's confirmed stopped (or wasn't running to begin with)."""
    running, pid = pidfile.tracker_status()
    if not running:
        return True

    try:
        if platform.system() == "Windows":
            os.kill(pid, signal.CTRL_BREAK_EVENT)
        else:
            os.kill(pid, signal.SIGINT)
    except (OSError, ProcessLookupError):
        return True

    proc = _children.pop(pid, None)
    if proc is not None:
        try:
            proc.wait(timeout=timeout)
            return True
        except subprocess.TimeoutExpired:
            return False

    # Started by a different process (e.g. a prior server run) — no Popen
    # handle to wait() on, so fall back to polling. A lingering zombie in
    # this case is reaped once its original parent process exits.
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pidfile.is_process_running(pid):
            return True
        time.sleep(0.1)
    return False
