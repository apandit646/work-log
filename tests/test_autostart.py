import subprocess

import pytest

from daylog import autostart


# --- Linux: real file write, subprocess mocked (no real systemd bus here) --


def test_install_linux_writes_a_valid_unit_file(daylog_home, monkeypatch, tmp_path):
    monkeypatch.setattr(autostart.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))

    ok, message = autostart.install_linux()

    assert ok is True
    unit_path = tmp_path / ".config" / "systemd" / "user" / "daylog-tracker.service"
    assert unit_path.exists()
    contents = unit_path.read_text(encoding="utf-8")
    assert "[Unit]" in contents and "[Service]" in contents and "[Install]" in contents
    assert "daylog.cli track" in contents
    assert str(unit_path) in message


def test_install_linux_reports_failure_when_systemctl_is_missing(daylog_home, monkeypatch, tmp_path):
    monkeypatch.setattr(autostart.Path, "home", staticmethod(lambda: tmp_path))

    def fake_run(*args, **kwargs):
        raise FileNotFoundError("systemctl not found")

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)

    ok, message = autostart.install_linux()

    assert ok is False
    assert "systemctl" in message
    # The unit file is still written even if enabling it failed.
    unit_path = tmp_path / ".config" / "systemd" / "user" / "daylog-tracker.service"
    assert unit_path.exists()


def test_install_linux_reports_systemctl_error_output(daylog_home, monkeypatch, tmp_path):
    monkeypatch.setattr(autostart.Path, "home", staticmethod(lambda: tmp_path))

    def fake_run(cmd, check, capture_output, text):
        if "enable" in cmd:
            raise subprocess.CalledProcessError(1, cmd, stderr="Unit not found.")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)

    ok, message = autostart.install_linux()
    assert ok is False
    assert "Unit not found" in message


def test_uninstall_linux_removes_existing_unit_file(daylog_home, monkeypatch, tmp_path):
    monkeypatch.setattr(autostart.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))

    autostart.install_linux()
    unit_path = tmp_path / ".config" / "systemd" / "user" / "daylog-tracker.service"
    assert unit_path.exists()

    ok, message = autostart.uninstall_linux()
    assert ok is True
    assert not unit_path.exists()
    assert "Removed" in message


def test_uninstall_linux_is_a_noop_when_nothing_installed(daylog_home, monkeypatch, tmp_path):
    monkeypatch.setattr(autostart.Path, "home", staticmethod(lambda: tmp_path))
    monkeypatch.setattr(autostart.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0))

    ok, message = autostart.uninstall_linux()
    assert ok is True
    assert "No" in message


# --- Windows: schtasks doesn't exist on this sandbox, so fully mocked -----


def test_install_windows_builds_correct_schtasks_command(monkeypatch):
    captured = {}

    def fake_run(cmd, check, capture_output, text):
        captured["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)
    monkeypatch.setattr(autostart, "_pythonw_path", lambda: r"C:\Python\pythonw.exe")

    ok, message = autostart.install_windows()

    assert ok is True
    cmd = captured["cmd"]
    assert cmd[0] == "schtasks"
    assert "/create" in cmd
    tr_index = cmd.index("/tr")
    assert "pythonw.exe" in cmd[tr_index + 1]
    assert "daylog.cli track" in cmd[tr_index + 1]
    assert "/sc" in cmd and "onlogon" in cmd


def test_install_windows_reports_schtasks_missing(monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("no schtasks")

    monkeypatch.setattr(autostart.subprocess, "run", fake_run)
    ok, message = autostart.install_windows()
    assert ok is False
    assert "schtasks" in message


def test_uninstall_windows_removes_task(monkeypatch):
    monkeypatch.setattr(
        autostart.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0)
    )
    ok, message = autostart.uninstall_windows()
    assert ok is True
    assert "Removed" in message


def test_uninstall_windows_missing_task_is_still_ok(monkeypatch):
    monkeypatch.setattr(
        autostart.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 1)
    )
    ok, message = autostart.uninstall_windows()
    assert ok is True
    assert "No scheduled task" in message


def test_pythonw_path_falls_back_to_interpreter_when_pythonw_missing(monkeypatch, tmp_path):
    fake_python = tmp_path / "python.exe"
    fake_python.write_text("", encoding="utf-8")
    monkeypatch.setattr(autostart.sys, "executable", str(fake_python))
    assert autostart._pythonw_path() == str(fake_python)


def test_pythonw_path_prefers_pythonw_when_present(monkeypatch, tmp_path):
    fake_python = tmp_path / "python.exe"
    fake_pythonw = tmp_path / "pythonw.exe"
    fake_python.write_text("", encoding="utf-8")
    fake_pythonw.write_text("", encoding="utf-8")
    monkeypatch.setattr(autostart.sys, "executable", str(fake_python))
    assert autostart._pythonw_path() == str(fake_pythonw)


# --- platform dispatch ----------------------------------------------------


def test_install_dispatches_by_platform(monkeypatch):
    monkeypatch.setattr(autostart.platform, "system", lambda: "Darwin")
    ok, message = autostart.install()
    assert ok is False
    assert "Darwin" in message


def test_uninstall_dispatches_by_platform(monkeypatch):
    monkeypatch.setattr(autostart.platform, "system", lambda: "Darwin")
    ok, message = autostart.uninstall()
    assert ok is False
