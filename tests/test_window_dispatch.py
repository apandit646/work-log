"""Tests the platform-selection logic in collectors/window.py.

The Linux and Windows backend *classes* can both be constructed on any OS
(neither touches an OS API in __init__), so we can verify dispatch for all
three platforms without needing to actually be on that platform. Exercising
the *methods* of the "wrong" platform's backend is deliberately not done
here — that's real OS interaction, covered separately (mocked) for Linux in
test_linux_backend.py and via pure-logic tests for Windows in
test_windows_backend.py.
"""
import pytest

from daylog.collectors import linux, window, windows


@pytest.fixture(autouse=True)
def _reset_backend_after_each_test():
    yield
    window.reset_backend()


def test_dispatches_to_linux_backend(monkeypatch):
    window.reset_backend()
    monkeypatch.setattr(window.platform, "system", lambda: "Linux")
    backend = window.get_backend()
    assert isinstance(backend, linux.LinuxBackend)


def test_dispatches_to_windows_backend(monkeypatch):
    window.reset_backend()
    monkeypatch.setattr(window.platform, "system", lambda: "Windows")
    backend = window.get_backend()
    assert isinstance(backend, windows.WindowsBackend)


def test_unsupported_platform_degrades_gracefully(monkeypatch):
    window.reset_backend()
    monkeypatch.setattr(window.platform, "system", lambda: "Darwin")
    backend = window.get_backend()
    assert isinstance(backend, window.UnsupportedBackend)
    assert backend.active_window() is None
    assert backend.idle_seconds() == 0.0


def test_backend_is_cached_across_calls(monkeypatch):
    window.reset_backend()
    monkeypatch.setattr(window.platform, "system", lambda: "Linux")
    first = window.get_backend()
    second = window.get_backend()
    assert first is second


def test_wayland_warning_present_when_session_type_is_wayland(monkeypatch):
    monkeypatch.setattr(window.platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert window.wayland_warning() is not None


def test_wayland_warning_absent_on_x11(monkeypatch):
    monkeypatch.setattr(window.platform, "system", lambda: "Linux")
    monkeypatch.setenv("XDG_SESSION_TYPE", "x11")
    assert window.wayland_warning() is None


def test_wayland_warning_absent_on_windows(monkeypatch):
    monkeypatch.setattr(window.platform, "system", lambda: "Windows")
    monkeypatch.setenv("XDG_SESSION_TYPE", "wayland")
    assert window.wayland_warning() is None
