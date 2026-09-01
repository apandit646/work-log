"""Platform-independent active-window and idle-time interface.

    active_window() -> WindowSample | None
    idle_seconds()  -> float

The concrete backend is chosen once, lazily, based on platform.system().
windows.py and linux.py each implement the same WindowBackend protocol;
adding macos.py later means writing one more class and one more branch in
_select_backend() — nothing else in the codebase needs to change.
"""
from __future__ import annotations

import os
import platform
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class WindowSample:
    app: str
    title: str


class WindowBackend:
    """Protocol implemented by windows.WindowsBackend and linux.LinuxBackend."""

    def active_window(self) -> Optional[WindowSample]:
        raise NotImplementedError

    def idle_seconds(self) -> float:
        raise NotImplementedError


class UnsupportedBackend(WindowBackend):
    """Used on platforms with no implementation yet (e.g. macOS for now).

    Tracking degrades to "nothing recorded" rather than crashing.
    """

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def active_window(self) -> Optional[WindowSample]:
        return None

    def idle_seconds(self) -> float:
        return 0.0


def _select_backend() -> WindowBackend:
    system = platform.system()
    if system == "Windows":
        from . import windows as _impl

        return _impl.WindowsBackend()
    if system == "Linux":
        from . import linux as _impl

        return _impl.LinuxBackend()
    return UnsupportedBackend(f"platform {system!r} is not supported yet (only Windows and Linux/X11)")


_backend: Optional[WindowBackend] = None


def get_backend() -> WindowBackend:
    global _backend
    if _backend is None:
        _backend = _select_backend()
    return _backend


def reset_backend() -> None:
    """Forces the next get_backend() call to re-select. Used by tests."""
    global _backend
    _backend = None


def active_window() -> Optional[WindowSample]:
    return get_backend().active_window()


def idle_seconds() -> float:
    return get_backend().idle_seconds()


def wayland_warning() -> Optional[str]:
    """A human-readable warning if running under Wayland, else None.

    xdotool/xprop (and often xprintidle) only see X11/XWayland windows, so
    under a pure-Wayland session window titles will silently come back
    empty rather than raising — this is the one place we surface that.
    """
    if platform.system() != "Linux":
        return None
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return (
            "Wayland session detected — window titles will be unavailable. "
            "Switch to an Xorg/X11 session for full tracking, or accept idle-only data."
        )
    return None
