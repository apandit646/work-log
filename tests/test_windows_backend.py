"""windows.py never touches ctypes.windll at import time (only inside method
bodies), so it can be imported and its pure-Python helper tested on any OS.
Exercising WindowsBackend's actual methods requires real Windows and is not
attempted here — see the platform-dispatch test for backend selection."""
from daylog.collectors.windows import app_name_from_exe_path


def test_strips_directory_and_exe_suffix():
    assert app_name_from_exe_path(r"C:\Program Files\Code\Code.exe") == "Code"


def test_case_insensitive_exe_suffix():
    assert app_name_from_exe_path(r"C:\tools\thing.EXE") == "thing"


def test_forward_slash_path():
    assert app_name_from_exe_path("C:/Users/me/AppData/app.exe") == "app"


def test_no_extension_left_as_is():
    assert app_name_from_exe_path(r"C:\weird\noext") == "noext"


def test_empty_path_falls_back_to_unknown():
    assert app_name_from_exe_path("") == "Unknown"
