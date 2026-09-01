"""argparse entry point: init, doctor, track, report, status, ui.

init, doctor, track, and status are implemented. report and ui are wired
up as real subcommands (so `daylog --help` and `daylog <cmd> --help`
already show the full shape of the tool) but print a friendly "not
implemented yet" message and exit 1 instead of doing anything — never an
unhandled traceback.
"""
from __future__ import annotations

import argparse
import os
import platform
import shutil
import signal
import sys
from typing import Callable, Optional

from . import pidfile, storage
from .collectors import window as window_collector
from .config import ConfigError, config_path, default_config, load_config, save_config
from .paths import db_path
from .tracker import Tracker


def cmd_init(args: argparse.Namespace) -> int:
    cfg_path = config_path()
    if cfg_path.exists() and not args.force:
        print(f"Config already exists at {cfg_path} (use --force to overwrite).")
    else:
        save_config(default_config(), cfg_path)
        print(f"Wrote default config to {cfg_path}")
        print("Edit it to point at your real repos and calendar before running 'daylog track'.")

    try:
        with storage.open_db() as conn:
            applied = storage.migrate(conn)
    except Exception as exc:
        print(f"Failed to set up the database at {db_path()}: {exc}")
        return 1

    if applied:
        print(f"Database ready at {db_path()} (applied migrations: {applied})")
    else:
        print(f"Database already up to date at {db_path()}")
    return 0


def _check(label: str, ok: bool, detail: str = "") -> bool:
    mark = "PASS" if ok else "FAIL"
    line = f"[{mark}] {label}"
    if detail:
        line += f" — {detail}"
    print(line)
    return ok


def _doctor_windows(results: list[bool]) -> None:
    try:
        import ctypes

        ctypes.windll.user32  # noqa: B018 — attribute access is the check
        results.append(_check("Windows window-tracking API (user32/kernel32 via ctypes)", True))
    except Exception as exc:
        results.append(
            _check("Windows window-tracking API (user32/kernel32 via ctypes)", False, str(exc))
        )


def _doctor_linux(results: list[bool]) -> None:
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
    _check(
        "xprintidle available (optional; falls back to X11 screensaver extension)",
        has_xprintidle,
        "" if has_xprintidle else "not found — will use the ctypes/X11 fallback instead",
    )


def cmd_doctor(args: argparse.Namespace) -> int:
    results: list[bool] = []

    py_ok = sys.version_info >= (3, 9)
    results.append(_check("Python >= 3.9", py_ok, f"found {platform.python_version()}"))

    git_path = shutil.which("git")
    results.append(_check("git executable on PATH", git_path is not None, git_path or "not found — install git"))

    system = platform.system()
    if system == "Windows":
        _doctor_windows(results)
    elif system == "Linux":
        _doctor_linux(results)
    else:
        results.append(
            _check(
                f"Platform '{system}' window-tracking",
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

    print()
    all_ok = all(results)
    print("All checks passed." if all_ok else "Some checks failed — see FAIL lines above.")
    return 0 if all_ok else 1


def cmd_track(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Cannot start tracking: {exc}")
        return 1

    running, running_pid = pidfile.tracker_status()
    if running:
        print(f"Tracker is already running (pid {running_pid}). Nothing to do.")
        return 1

    warning = window_collector.wayland_warning()
    if warning:
        print(f"Warning: {warning}")

    pidfile.write_pidfile()
    stop_requested = False

    def _request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    signal.signal(signal.SIGINT, _request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_stop)

    print(f"Tracking started (pid {os.getpid()}, polling every {cfg.tracking.poll_interval_seconds}s).")
    print("Press Ctrl+C to stop.")
    try:
        with storage.open_db() as conn:
            tracker = Tracker(conn, cfg)
            tracker.run_forever(stop=lambda: stop_requested)
    finally:
        pidfile.remove_pidfile()
    print("Tracking stopped.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    running, pid = pidfile.tracker_status()
    print(f"Tracker is running (pid {pid})." if running else "Tracker is not running.")

    try:
        with storage.open_db() as conn:
            last_end = storage.get_last_activity_end(conn)
    except Exception as exc:
        print(f"Could not read the database: {exc}")
        return 1

    print(f"Last sample recorded at {last_end}" if last_end else "No activity has been recorded yet.")
    return 0


def _not_implemented(name: str, phase: str) -> Callable[[argparse.Namespace], int]:
    def _cmd(args: argparse.Namespace) -> int:
        print(f"'daylog {name}' isn't implemented yet — it lands in {phase}.")
        print("Run 'daylog doctor' to check your setup in the meantime.")
        return 1

    return _cmd


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="daylog", description="Local-first daily activity tracker and timesheet drafter."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_init = sub.add_parser("init", help="Create the default config.json and database.")
    p_init.add_argument("--force", action="store_true", help="Overwrite an existing config.json.")
    p_init.set_defaults(func=cmd_init)

    p_doctor = sub.add_parser("doctor", help="Check that daylog can run correctly on this machine.")
    p_doctor.set_defaults(func=cmd_doctor)

    p_track = sub.add_parser("track", help="Run the background window/idle tracker.")
    p_track.set_defaults(func=cmd_track)

    p_report = sub.add_parser("report", help="Generate today's (or another day's) report.")
    p_report.add_argument("--date", help="YYYY-MM-DD, defaults to today")
    p_report.add_argument("--copy", action="store_true", help="Copy the timesheet draft to the clipboard")
    p_report.add_argument("--out", help="Write the Markdown report to this file")
    p_report.add_argument("--json", action="store_true", help="Print the report as JSON instead of Markdown")
    p_report.set_defaults(func=_not_implemented("report", "Phase 5"))

    p_status = sub.add_parser("status", help="Show whether the tracker is running and when it last sampled.")
    p_status.set_defaults(func=cmd_status)

    p_ui = sub.add_parser("ui", help="Start the local web UI and open it in a browser.")
    p_ui.set_defaults(func=_not_implemented("ui", "Phase 7"))

    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # last-resort guard: never an unhandled traceback
        print(f"daylog: unexpected error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
