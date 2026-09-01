"""Linux backend tested with the platform layer (subprocess, shutil.which)
mocked, so this passes on any OS/CI runner — no real X server needed."""
import subprocess

from daylog.collectors import linux


def _fake_which(available):
    def _which(name):
        return f"/usr/bin/{name}" if name in available else None

    return _which


def _fake_run(outputs):
    """outputs: dict cmd[0] -> (returncode, stdout). Matches on the argv[0]
    binary name plus, for xdotool, the subcommand (argv[1])."""

    def _run(cmd, capture_output, text, timeout):
        key = cmd[0] if cmd[0] != "xdotool" else f"xdotool {cmd[1]}"
        returncode, stdout = outputs.get(key, (1, ""))
        return subprocess.CompletedProcess(cmd, returncode, stdout=stdout, stderr="")

    return _run


def test_active_window_via_xdotool(monkeypatch):
    monkeypatch.setattr(linux.shutil, "which", _fake_which({"xdotool"}))
    monkeypatch.setattr(
        linux.subprocess,
        "run",
        _fake_run(
            {
                "xdotool getactivewindow": (0, "12345\n"),
                "xdotool getwindowname": (0, "main.py - Visual Studio Code\n"),
                "xdotool getwindowpid": (0, "999999\n"),  # deliberately nonexistent pid
                "xdotool getwindowclassname": (0, "code\n"),
            }
        ),
    )
    sample = linux._active_window_xdotool()
    assert sample is not None
    assert sample.title == "main.py - Visual Studio Code"
    # /proc/999999/comm won't exist, so it must fall back to getwindowclassname.
    assert sample.app == "code"


def test_active_window_xdotool_absent_returns_none(monkeypatch):
    monkeypatch.setattr(linux.shutil, "which", _fake_which(set()))
    assert linux._active_window_xdotool() is None


def test_active_window_via_xprop(monkeypatch):
    monkeypatch.setattr(linux.shutil, "which", _fake_which({"xprop"}))

    xprop_info = (
        'WM_CLASS(STRING) = "code", "Code"\n'
        '_NET_WM_NAME(UTF8_STRING) = "main.py - Visual Studio Code"\n'
    )

    def _run(cmd, capture_output, text, timeout):
        if cmd[:2] == ["xprop", "-root"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="_NET_ACTIVE_WINDOW(WINDOW): window id # 0x2c00007\n", stderr="")
        if cmd[:2] == ["xprop", "-id"]:
            return subprocess.CompletedProcess(cmd, 0, stdout=xprop_info, stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(linux.subprocess, "run", _run)

    sample = linux._active_window_xprop()
    assert sample is not None
    assert sample.app == "Code"
    assert sample.title == "main.py - Visual Studio Code"


def test_active_window_xprop_no_tool_returns_none(monkeypatch):
    monkeypatch.setattr(linux.shutil, "which", _fake_which(set()))
    assert linux._active_window_xprop() is None


def test_active_window_falls_back_to_xprop_when_xdotool_fails(monkeypatch):
    monkeypatch.setattr(linux.shutil, "which", _fake_which({"xdotool", "xprop"}))

    def _run(cmd, capture_output, text, timeout):
        if cmd[0] == "xdotool":
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="no display")
        if cmd[:2] == ["xprop", "-root"]:
            return subprocess.CompletedProcess(cmd, 0, stdout="window id # 0x1\n", stderr="")
        if cmd[:2] == ["xprop", "-id"]:
            return subprocess.CompletedProcess(cmd, 0, stdout='WM_NAME(STRING) = "a title"\n', stderr="")
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="")

    monkeypatch.setattr(linux.subprocess, "run", _run)

    backend = linux.LinuxBackend()
    sample = backend.active_window()
    assert sample is not None
    assert sample.title == "a title"


def test_idle_seconds_via_xprintidle(monkeypatch):
    monkeypatch.setattr(linux.shutil, "which", _fake_which({"xprintidle"}))
    monkeypatch.setattr(linux.subprocess, "run", _fake_run({"xprintidle": (0, "4200\n")}))
    assert linux._idle_seconds_xprintidle() == 4.2


def test_idle_seconds_no_tools_falls_back_to_zero(monkeypatch):
    monkeypatch.setattr(linux.shutil, "which", _fake_which(set()))
    backend = linux.LinuxBackend()
    # No xprintidle and (almost certainly) no libXss in this sandbox — must
    # degrade to 0.0, never raise.
    assert backend.idle_seconds() >= 0.0


def test_run_handles_missing_binary_gracefully():
    assert linux._run(["definitely-not-a-real-binary-xyz"]) is None


def test_run_handles_timeout_gracefully(monkeypatch):
    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="xdotool", timeout=2)

    monkeypatch.setattr(linux.subprocess, "run", _raise_timeout)
    assert linux._run(["xdotool", "getactivewindow"]) is None
