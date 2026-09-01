"""Linux/X11 active-window and idle-time backend.

Active window: tries `xdotool` first (gives us window id, title, and pid
in one tool), falls back to `xprop` (window id + WM_CLASS/_NET_WM_NAME)
if xdotool isn't installed.

Idle time: tries `xprintidle` first, falls back to the X11 screensaver
extension (libXss) via ctypes.

Every helper here returns None on any failure (tool missing, no display,
X error, timeout) instead of raising — `daylog doctor` is where the user
finds out *why* nothing is being recorded; the tracker loop just quietly
gets no sample and moves on. Under Wayland none of this will see real
window titles; that's surfaced separately by window.wayland_warning().
"""
from __future__ import annotations

import re
import shutil
import subprocess
from typing import List, Optional

from .window import WindowBackend, WindowSample

_TIMEOUT = 2


def _run(cmd: List[str]) -> Optional[str]:
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip()


def _app_name_from_pid(pid_str: str) -> Optional[str]:
    try:
        pid = int(pid_str)
    except ValueError:
        return None
    try:
        with open(f"/proc/{pid}/comm", encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def _active_window_xdotool() -> Optional[WindowSample]:
    if shutil.which("xdotool") is None:
        return None
    window_id = _run(["xdotool", "getactivewindow"])
    if not window_id:
        return None
    title = _run(["xdotool", "getwindowname", window_id]) or ""
    pid_str = _run(["xdotool", "getwindowpid", window_id])
    app = _app_name_from_pid(pid_str) if pid_str else None
    if not app:
        app = _run(["xdotool", "getwindowclassname", window_id]) or "Unknown"
    return WindowSample(app=app, title=title)


_XPROP_ACTIVE_ID_RE = re.compile(r"(0x[0-9a-fA-F]+)")
_XPROP_CLASS_RE = re.compile(r'WM_CLASS\(STRING\) = "([^"]*)", "([^"]*)"')
_XPROP_NET_NAME_RE = re.compile(r'_NET_WM_NAME\(UTF8_STRING\) = "(.*)"')
_XPROP_WM_NAME_RE = re.compile(r'WM_NAME\(STRING\) = "(.*)"')


def _active_window_xprop() -> Optional[WindowSample]:
    if shutil.which("xprop") is None:
        return None
    root = _run(["xprop", "-root", "_NET_ACTIVE_WINDOW"])
    if not root:
        return None
    match = _XPROP_ACTIVE_ID_RE.search(root)
    if not match:
        return None
    window_id = match.group(1)

    info = _run(["xprop", "-id", window_id, "WM_CLASS", "_NET_WM_NAME", "WM_NAME"])
    if info is None:
        return None

    app = "Unknown"
    class_match = _XPROP_CLASS_RE.search(info)
    if class_match:
        app = class_match.group(2) or class_match.group(1)

    title = ""
    name_match = _XPROP_NET_NAME_RE.search(info) or _XPROP_WM_NAME_RE.search(info)
    if name_match:
        title = name_match.group(1)

    return WindowSample(app=app, title=title)


def _idle_seconds_xprintidle() -> Optional[float]:
    if shutil.which("xprintidle") is None:
        return None
    out = _run(["xprintidle"])
    if not out:
        return None
    try:
        return int(out) / 1000.0
    except ValueError:
        return None


def _idle_seconds_xss() -> Optional[float]:
    """Fallback idle time via the X11 screensaver extension (libXss)."""
    try:
        import ctypes
        import ctypes.util

        x11_path = ctypes.util.find_library("X11")
        xss_path = ctypes.util.find_library("Xss")
        if not x11_path or not xss_path:
            return None
        xlib = ctypes.CDLL(x11_path)
        xss = ctypes.CDLL(xss_path)

        xlib.XOpenDisplay.restype = ctypes.c_void_p
        display = xlib.XOpenDisplay(None)
        if not display:
            return None
        try:
            root = xlib.XDefaultRootWindow(display)

            class _XScreenSaverInfo(ctypes.Structure):
                _fields_ = [
                    ("window", ctypes.c_ulong),
                    ("state", ctypes.c_int),
                    ("kind", ctypes.c_int),
                    ("since", ctypes.c_ulong),
                    ("idle", ctypes.c_ulong),
                    ("eventMask", ctypes.c_ulong),
                ]

            xss.XScreenSaverAllocInfo.restype = ctypes.POINTER(_XScreenSaverInfo)
            info = xss.XScreenSaverAllocInfo()
            if not info:
                return None
            xss.XScreenSaverQueryInfo(display, root, info)
            idle_ms = info.contents.idle
            xlib.XFree(info)
            return idle_ms / 1000.0
        finally:
            xlib.XCloseDisplay(display)
    except Exception:
        return None


class LinuxBackend(WindowBackend):
    def active_window(self) -> Optional[WindowSample]:
        sample = _active_window_xdotool()
        if sample is not None:
            return sample
        return _active_window_xprop()

    def idle_seconds(self) -> float:
        idle = _idle_seconds_xprintidle()
        if idle is None:
            idle = _idle_seconds_xss()
        return idle if idle is not None else 0.0
