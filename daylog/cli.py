"""argparse entry point: init, doctor, track, report, status, ui, tray,
install, uninstall.

All subcommands are implemented.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json as _json
import os
import signal
import sys
from pathlib import Path
from typing import Optional

from . import autostart, clipboard, doctor, pidfile, storage, tracker_process
from .collectors import window as window_collector
from .config import ConfigError, config_path, default_config, load_config, save_config
from .paths import db_path
from .report import builder as report_builder
from .report import render as report_render
from .storage import StorageError
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


def cmd_doctor(args: argparse.Namespace) -> int:
    results = doctor.run_checks()
    for r in results:
        mark = "PASS" if r["ok"] else "FAIL"
        line = f"[{mark}] {r['label']}"
        if r["detail"]:
            line += f" — {r['detail']}"
        print(line)

    print()
    all_ok = doctor.all_required_ok(results)
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

    stop_requested = False

    def _request_stop(signum, frame):
        nonlocal stop_requested
        stop_requested = True

    # Register handlers *before* writing the pidfile: a signal arriving in
    # the gap between the two would otherwise get Python's default
    # SIGINT/SIGTERM handling (an immediate, ungraceful exit that skips the
    # try/finally below and leaves the pidfile behind).
    signal.signal(signal.SIGINT, _request_stop)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, _request_stop)
    if hasattr(signal, "SIGBREAK"):  # Windows: raised by CTRL_BREAK_EVENT,
        signal.signal(signal.SIGBREAK, _request_stop)  # used by tracker_process.stop_tracker()

    pidfile.write_pidfile()

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


def cmd_report(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Cannot generate report: {exc}")
        return 1

    day = args.date or _dt.date.today().isoformat()
    try:
        _dt.date.fromisoformat(day)
    except ValueError:
        print(f"Invalid --date {day!r}: expected YYYY-MM-DD")
        return 1

    try:
        with storage.open_db() as conn:
            existing = storage.get_day_summary(conn, day)
            if existing and existing.status == "submitted":
                print(f"{day} is already submitted — its summary is frozen.")
                print("Reopen it first (daylog ui, or POST /api/days/{date}/reopen) to regenerate.")
                return 1
            if existing and storage.has_unsaved_edits(conn, day):
                print(f"Note: {day} has hand-edited text that differs from the last generated draft.")
                print("Regenerating refreshes the data sections but will NOT touch your edited text.\n")

            report = report_builder.generate_report(cfg, conn, day)
            markdown = report_render.render_markdown(report)
            # Only the timesheet-paste-ready bullets are persisted as
            # generated_md — render_markdown()'s full multi-section report
            # (printed/--out below) isn't something you'd paste into an
            # office form, so it's never what the editable summary holds.
            # draft_text is the plain rule-based join, or the LLM-polished
            # rewrite of it if config.llm.enabled succeeded.
            storage.save_generated_summary(conn, day, report.draft_text)
    except StorageError as exc:
        print(f"Could not generate report: {exc}")
        return 1

    if not report.git_available and report.git_error:
        print(f"Warning: {report.git_error}", file=sys.stderr)
    if not report.calendar_available and report.calendar_error:
        print(f"Warning: {report.calendar_error}", file=sys.stderr)
    if report.llm_used:
        print("Timesheet draft polished by Claude.", file=sys.stderr)
    elif cfg.llm.enabled and report.llm_error:
        print(f"Warning: LLM polishing skipped: {report.llm_error}", file=sys.stderr)

    if args.json:
        print(_json.dumps(report_render.render_json(report), indent=2))
    else:
        print(markdown)

    if args.out:
        Path(args.out).write_text(markdown, encoding="utf-8")
        print(f"\nWrote report to {args.out}")

    if args.copy:
        ok, error = clipboard.copy_to_clipboard(report.draft_text)
        if ok:
            print("\nTimesheet draft copied to clipboard.")
        else:
            print(f"\nCould not copy to clipboard: {error}")

    return 0


def cmd_ui(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Cannot start the web UI: {exc}")
        return 1

    import uvicorn

    from .server.app import app as fastapi_app

    url = f"http://{cfg.server.host}:{cfg.server.port}/"
    print(f"Starting daylog UI at {url}")
    print("Press Ctrl+C to stop.")

    if not args.no_browser:
        import threading
        import webbrowser

        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    try:
        uvicorn.run(fastapi_app, host=cfg.server.host, port=cfg.server.port, log_level="warning")
    except OSError as exc:
        print(f"Could not start the server: {exc}")
        return 1
    return 0


def cmd_tray(args: argparse.Namespace) -> int:
    try:
        cfg = load_config()
    except ConfigError as exc:
        print(f"Cannot start the tray icon: {exc}")
        return 1

    from .tray import run_tray

    try:
        run_tray(cfg)
    except RuntimeError as exc:
        print(str(exc))
        return 1
    except Exception as exc:  # last-resort guard — see the module docstring
        print(f"Tray icon failed to start: {exc}")
        print("daylog works fully without it — use 'daylog track' and 'daylog ui' instead.")
        return 1
    return 0


def cmd_install(args: argparse.Namespace) -> int:
    ok, message = autostart.install()
    print(message)
    return 0 if ok else 1


def cmd_uninstall(args: argparse.Namespace) -> int:
    ok, message = autostart.uninstall()
    print(message)
    running, _ = pidfile.tracker_status()
    if running:
        if tracker_process.stop_tracker():
            print("Stopped the currently running tracker.")
        else:
            print("Could not confirm the currently running tracker stopped in time.")
    return 0 if ok else 1


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
    p_report.set_defaults(func=cmd_report)

    p_status = sub.add_parser("status", help="Show whether the tracker is running and when it last sampled.")
    p_status.set_defaults(func=cmd_status)

    p_ui = sub.add_parser("ui", help="Start the local web UI and open it in a browser.")
    p_ui.add_argument("--no-browser", action="store_true", help="Don't automatically open a browser window")
    p_ui.set_defaults(func=cmd_ui)

    p_tray = sub.add_parser(
        "tray", help="Show a system tray icon (optional; requires the 'tray' extra)."
    )
    p_tray.set_defaults(func=cmd_tray)

    p_install = sub.add_parser(
        "install", help="Set daylog track to run automatically in the background (systemd/Task Scheduler)."
    )
    p_install.set_defaults(func=cmd_install)

    p_uninstall = sub.add_parser("uninstall", help="Remove the autostart entry created by 'daylog install'.")
    p_uninstall.set_defaults(func=cmd_uninstall)

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
