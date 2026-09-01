"""Optional system tray icon: shows whether the tracker is running and
opens the web UI on click. Entirely optional by design — every other
part of daylog works without it, because tray/notification-area support
is unreliable on some Linux desktop environments (no StatusNotifierItem
support, no DBus session, etc). pystray/Pillow are only imported when
`daylog tray` actually runs, from the `tray` extra
(`pip install daylog[tray]`) — never a hard dependency of the core tool.
"""
from __future__ import annotations

import threading
import time
import webbrowser
from typing import TYPE_CHECKING, Any

from . import pidfile, tracker_process

if TYPE_CHECKING:
    from .config import Config

_POLL_SECONDS = 5
_RUNNING_COLOR = (58, 107, 82, 255)
_STOPPED_COLOR = (150, 150, 150, 255)


def _make_icon_image(running: bool) -> Any:
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((8, 8, size - 8, size - 8), fill=_RUNNING_COLOR if running else _STOPPED_COLOR)
    return image


def _status_title(running: bool) -> str:
    return "daylog — tracking" if running else "daylog — not tracking"


def build_icon(config: "Config") -> Any:
    """Constructs (but doesn't run) the pystray Icon. Split out from
    run_tray() so tests can exercise the menu/refresh logic without
    calling the blocking icon.run()."""
    import pystray

    url = f"http://{config.server.host}:{config.server.port}/"

    def open_ui(icon=None, item=None) -> None:
        webbrowser.open(url)

    def toggle_tracking(icon=None, item=None) -> None:
        running, _ = pidfile.tracker_status()
        if running:
            tracker_process.stop_tracker()
        else:
            tracker_process.start_tracker()
        refresh(icon)

    def quit_tray(icon=None, item=None) -> None:
        icon.stop()

    running, _ = pidfile.tracker_status()
    toggle_item = pystray.MenuItem(
        "Stop tracking" if running else "Start tracking", toggle_tracking
    )
    menu = pystray.Menu(
        pystray.MenuItem("Open daylog", open_ui, default=True),
        toggle_item,
        pystray.MenuItem("Quit", quit_tray),
    )
    icon = pystray.Icon("daylog", _make_icon_image(running), _status_title(running), menu)

    def refresh(icon) -> None:
        running, _ = pidfile.tracker_status()
        icon.icon = _make_icon_image(running)
        icon.title = _status_title(running)
        icon.menu = pystray.Menu(
            pystray.MenuItem("Open daylog", open_ui, default=True),
            pystray.MenuItem("Stop tracking" if running else "Start tracking", toggle_tracking),
            pystray.MenuItem("Quit", quit_tray),
        )

    icon._daylog_refresh = refresh  # exposed for the poll loop and for tests
    return icon


def run_tray(config: "Config") -> None:
    try:
        import pystray  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            "pystray is not installed. Install the optional tray extra with "
            "'pip install daylog[tray]', or just use 'daylog track' and 'daylog ui' directly — "
            "daylog works fully without the tray icon."
        ) from exc

    icon = build_icon(config)

    def poll() -> None:
        while True:
            time.sleep(_POLL_SECONDS)
            try:
                icon._daylog_refresh(icon)
            except Exception:
                return  # icon has stopped/been torn down

    threading.Thread(target=poll, daemon=True).start()
    icon.run()
