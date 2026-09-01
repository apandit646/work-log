"""Windows active-window and idle-time backend, via ctypes only (no pywin32).

ctypes.windll is only ever touched inside method bodies below, never at
module import time, so this file can still be imported (and its pure
helper function tested) on non-Windows platforms — actually calling
WindowsBackend's methods on a non-Windows OS will raise, same as any
Windows-only API would.
"""
from __future__ import annotations

import ctypes
from typing import Optional

from .window import WindowBackend, WindowSample

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

# Deliberately not importing ctypes.wintypes: we only need two integer
# types, and defining them by hand keeps this module import-safe everywhere.
_DWORD = ctypes.c_ulong
_UINT = ctypes.c_uint


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", _UINT), ("dwTime", _DWORD)]


def app_name_from_exe_path(path: str) -> str:
    """'C:\\Program Files\\Code\\Code.exe' -> 'Code'. Pure string logic, no OS calls."""
    name = path.rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if name.lower().endswith(".exe"):
        name = name[: -len(".exe")]
    return name or "Unknown"


class WindowsBackend(WindowBackend):
    def active_window(self) -> Optional[WindowSample]:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return None

        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value

        pid = _DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if not pid.value:
            return WindowSample(app="Unknown", title=title)

        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value)
        if not handle:
            # Usually a protected system process we're not allowed to query.
            return WindowSample(app="Unknown", title=title)
        try:
            size = _DWORD(260)
            path_buf = ctypes.create_unicode_buffer(size.value)
            ok = kernel32.QueryFullProcessImageNameW(handle, 0, path_buf, ctypes.byref(size))
            if not ok:
                return WindowSample(app="Unknown", title=title)
            return WindowSample(app=app_name_from_exe_path(path_buf.value), title=title)
        finally:
            kernel32.CloseHandle(handle)

    def idle_seconds(self) -> float:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return 0.0
        millis = kernel32.GetTickCount64() - info.dwTime
        return max(0.0, millis / 1000.0)
