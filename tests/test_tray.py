"""tray.py's real value proposition (per the spec) is that daylog works
fully without it, so the default test environment — pystray genuinely
not installed — exercises the important path. A lightweight fake pystray
module is used for the remaining tests so the menu/refresh logic itself
is still covered without needing a real display/tray protocol, which
this sandbox doesn't have anyway."""
import sys
import types

import pytest

from daylog import tray
from daylog.config import default_config


def test_run_tray_reports_missing_pystray_clearly(daylog_home, monkeypatch):
    # Ensure a real "pystray" isn't importable, whether or not it's
    # installed in this environment.
    monkeypatch.setitem(sys.modules, "pystray", None)

    with pytest.raises(RuntimeError) as exc_info:
        tray.run_tray(default_config())

    assert "pystray" in str(exc_info.value)
    assert "daylog works fully without" in str(exc_info.value)


class _FakeMenuItem:
    def __init__(self, text, action, default=False):
        self.text = text
        self.action = action
        self.default = default


class _FakeMenu:
    def __init__(self, *items):
        self.items = items


class _FakeIcon:
    def __init__(self, name, image, title, menu):
        self.name = name
        self.icon = image
        self.title = title
        self.menu = menu
        self.stopped = False

    def run(self):  # pragma: no cover - never called in these tests
        raise AssertionError("icon.run() should not be called by these tests")

    def stop(self):
        self.stopped = True


@pytest.fixture
def fake_pystray(monkeypatch):
    fake = types.ModuleType("pystray")
    fake.MenuItem = _FakeMenuItem
    fake.Menu = _FakeMenu
    fake.Icon = _FakeIcon
    monkeypatch.setitem(sys.modules, "pystray", fake)
    return fake


@pytest.fixture
def fake_pillow(monkeypatch):
    pil = types.ModuleType("PIL")
    image_mod = types.ModuleType("PIL.Image")
    draw_mod = types.ModuleType("PIL.ImageDraw")

    class _FakeImage:
        def __init__(self, *a, **k):
            pass

    class _FakeDraw:
        def __init__(self, image):
            pass

        def ellipse(self, *a, **k):
            pass

    image_mod.new = lambda *a, **k: _FakeImage()
    draw_mod.Draw = lambda image: _FakeDraw(image)
    pil.Image = image_mod
    pil.ImageDraw = draw_mod
    monkeypatch.setitem(sys.modules, "PIL", pil)
    monkeypatch.setitem(sys.modules, "PIL.Image", image_mod)
    monkeypatch.setitem(sys.modules, "PIL.ImageDraw", draw_mod)


def test_build_icon_shows_not_tracking_when_tracker_is_stopped(daylog_home, fake_pystray, fake_pillow):
    icon = tray.build_icon(default_config())
    assert "not tracking" in icon.title
    labels = [item.text for item in icon.menu.items]
    assert "Start tracking" in labels
    assert "Open daylog" in labels
    assert "Quit" in labels


def test_build_icon_shows_tracking_when_tracker_is_running(daylog_home, fake_pystray, fake_pillow, monkeypatch):
    monkeypatch.setattr(tray.pidfile, "tracker_status", lambda: (True, 12345))
    icon = tray.build_icon(default_config())
    assert "tracking" in icon.title and "not tracking" not in icon.title
    labels = [item.text for item in icon.menu.items]
    assert "Stop tracking" in labels


def test_refresh_updates_title_and_menu_after_status_change(daylog_home, fake_pystray, fake_pillow, monkeypatch):
    monkeypatch.setattr(tray.pidfile, "tracker_status", lambda: (False, None))
    icon = tray.build_icon(default_config())
    assert "not tracking" in icon.title

    monkeypatch.setattr(tray.pidfile, "tracker_status", lambda: (True, 999))
    icon._daylog_refresh(icon)
    assert "not tracking" not in icon.title
    labels = [item.text for item in icon.menu.items]
    assert "Stop tracking" in labels


def test_quit_menu_item_stops_the_icon(daylog_home, fake_pystray, fake_pillow):
    icon = tray.build_icon(default_config())
    quit_item = next(item for item in icon.menu.items if item.text == "Quit")
    quit_item.action(icon)
    assert icon.stopped is True


def test_open_ui_menu_item_opens_the_browser(daylog_home, fake_pystray, fake_pillow, monkeypatch):
    opened = {}
    monkeypatch.setattr(tray.webbrowser, "open", lambda url: opened.setdefault("url", url))

    cfg = default_config()
    cfg.server.port = 9999
    icon = tray.build_icon(cfg)
    open_item = next(item for item in icon.menu.items if item.text == "Open daylog")
    open_item.action(icon)

    assert opened["url"] == "http://127.0.0.1:9999/"


def test_toggle_tracking_starts_when_stopped(daylog_home, fake_pystray, fake_pillow, monkeypatch):
    calls = {"started": False}
    monkeypatch.setattr(tray.pidfile, "tracker_status", lambda: (False, None))
    monkeypatch.setattr(tray.tracker_process, "start_tracker", lambda: calls.__setitem__("started", True))

    icon = tray.build_icon(default_config())
    toggle_item = next(item for item in icon.menu.items if item.text == "Start tracking")
    toggle_item.action(icon)

    assert calls["started"] is True
