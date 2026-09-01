import json
import os
import subprocess
import sys
import time
from pathlib import Path

from daylog import pidfile
from daylog.cli import main
from daylog.paths import config_path, db_path

REPO_ROOT = Path(__file__).resolve().parents[1]


def test_init_creates_config_and_database(daylog_home, capsys):
    exit_code = main(["init"])
    assert exit_code == 0
    assert config_path().exists()
    assert db_path().exists()

    cfg = json.loads(config_path().read_text(encoding="utf-8"))
    assert cfg["server"]["host"] == "127.0.0.1"

    out = capsys.readouterr().out
    assert "Wrote default config" in out


def test_init_without_force_does_not_overwrite_existing_config(daylog_home, capsys):
    main(["init"])
    config_path().write_text('{"server": {"port": 9999}}', encoding="utf-8")

    main(["init"])  # no --force

    cfg = json.loads(config_path().read_text(encoding="utf-8"))
    assert cfg["server"]["port"] == 9999


def test_init_with_force_overwrites_existing_config(daylog_home):
    main(["init"])
    config_path().write_text('{"server": {"port": 9999}}', encoding="utf-8")

    main(["init", "--force"])

    cfg = json.loads(config_path().read_text(encoding="utf-8"))
    assert cfg["server"]["port"] == 8765


def test_doctor_runs_without_crashing_before_init(daylog_home, capsys):
    exit_code = main(["doctor"])
    # No config yet, so doctor must report FAIL (not raise) and exit non-zero.
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "config.json valid" in out
    assert "FAIL" in out


def test_doctor_runs_after_init(daylog_home, capsys):
    main(["init"])
    exit_code = main(["doctor"])
    out = capsys.readouterr().out
    assert "config.json valid" in out
    assert "database writable" in out
    assert exit_code in (0, 1)  # environment-dependent (e.g. missing xdotool in CI)


def test_ui_without_config_gives_a_clear_error(daylog_home, capsys):
    # Exercises only the early config-check path — daylog ui otherwise
    # blocks forever in uvicorn.run(), which is covered by a real
    # subprocess test instead (see test_cli_ui.py).
    exit_code = main(["ui"])
    assert exit_code == 1
    assert "daylog init" in capsys.readouterr().out


def test_status_before_anything_has_run(daylog_home, capsys):
    exit_code = main(["status"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Tracker is not running." in out
    assert "No activity has been recorded yet." in out


def test_status_reports_running_tracker(daylog_home, capsys):
    pidfile.write_pidfile()
    exit_code = main(["status"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert f"pid {os.getpid()}" in out


def test_track_refuses_to_start_a_second_instance(daylog_home, capsys):
    main(["init"])
    pidfile.write_pidfile()  # simulate an already-running tracker (this test process)

    exit_code = main(["track"])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "already running" in out


def test_track_writes_a_pidfile_and_a_hard_kill_is_detected_as_stopped(daylog_home):
    main(["init"])
    env = os.environ.copy()
    env["DAYLOG_HOME"] = str(daylog_home)

    proc = subprocess.Popen(
        [sys.executable, "-m", "daylog.cli", "track"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        pid_path = pidfile.pidfile_path()
        for _ in range(100):
            if pid_path.exists():
                break
            time.sleep(0.05)
        assert pid_path.exists(), "tracker subprocess never wrote a pidfile"

        running, pid = pidfile.tracker_status()
        assert running is True
        assert pid == proc.pid
    finally:
        proc.kill()
        proc.wait(timeout=5)

    # A hard kill (no chance to run the `finally: remove_pidfile()` cleanup)
    # must still be detected as "not running" on the next check, not stay
    # stuck reporting a dead pid as alive forever.
    running, pid = pidfile.tracker_status()
    assert (running, pid) == (False, None)


def test_install_and_uninstall_dispatch_through_autostart(daylog_home, capsys, monkeypatch):
    from daylog import autostart

    monkeypatch.setattr(autostart, "install", lambda: (True, "installed ok"))
    monkeypatch.setattr(autostart, "uninstall", lambda: (True, "uninstalled ok"))

    exit_code = main(["install"])
    assert exit_code == 0
    assert "installed ok" in capsys.readouterr().out

    exit_code = main(["uninstall"])
    assert exit_code == 0
    assert "uninstalled ok" in capsys.readouterr().out


def test_install_reports_failure_with_nonzero_exit(daylog_home, capsys, monkeypatch):
    from daylog import autostart

    monkeypatch.setattr(autostart, "install", lambda: (False, "could not install"))

    exit_code = main(["install"])
    assert exit_code == 1
    assert "could not install" in capsys.readouterr().out


def test_uninstall_also_stops_a_running_tracker(daylog_home, capsys, monkeypatch):
    from daylog import autostart, pidfile, tracker_process

    monkeypatch.setattr(autostart, "uninstall", lambda: (True, "uninstalled ok"))
    monkeypatch.setattr(pidfile, "tracker_status", lambda: (True, 4242))
    monkeypatch.setattr(tracker_process, "stop_tracker", lambda: True)

    exit_code = main(["uninstall"])
    assert exit_code == 0
    out = capsys.readouterr().out
    assert "uninstalled ok" in out
    assert "Stopped the currently running tracker." in out


def test_tray_without_pystray_installed_gives_a_clear_error(daylog_home, capsys):
    main(["init"])
    exit_code = main(["tray"])
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "pystray" in out
    assert "daylog works fully without" in out


def test_tray_without_config_gives_a_clear_error(daylog_home, capsys):
    exit_code = main(["tray"])
    assert exit_code == 1
    assert "daylog init" in capsys.readouterr().out
