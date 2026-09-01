import pytest


@pytest.fixture
def daylog_home(tmp_path, monkeypatch):
    """Redirect ~/.daylog to a temp dir so tests never touch the real one."""
    monkeypatch.setenv("DAYLOG_HOME", str(tmp_path))
    return tmp_path
