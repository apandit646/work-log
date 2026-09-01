import os
import subprocess
import sys
import time

from daylog import pidfile


def test_no_pidfile_means_not_running(daylog_home):
    assert pidfile.read_pidfile() is None
    running, pid = pidfile.tracker_status()
    assert (running, pid) == (False, None)


def test_current_process_is_reported_running(daylog_home):
    pidfile.write_pidfile()
    running, pid = pidfile.tracker_status()
    assert running is True
    assert pid == os.getpid()


def test_stale_pidfile_is_detected_and_cleared(daylog_home):
    # A pid essentially guaranteed not to exist on any real system.
    pidfile.pidfile_path().parent.mkdir(parents=True, exist_ok=True)
    pidfile.pidfile_path().write_text("999999999", encoding="utf-8")

    running, pid = pidfile.tracker_status()
    assert (running, pid) == (False, None)
    assert not pidfile.pidfile_path().exists()


def test_remove_pidfile_is_safe_when_missing(daylog_home):
    pidfile.remove_pidfile()  # must not raise


def test_is_process_running_tracks_a_real_subprocess(daylog_home):
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(5)"])
    try:
        # Give it a moment to actually start.
        for _ in range(50):
            if pidfile.is_process_running(proc.pid):
                break
            time.sleep(0.05)
        assert pidfile.is_process_running(proc.pid) is True
    finally:
        proc.terminate()
        proc.wait(timeout=5)

    assert pidfile.is_process_running(proc.pid) is False
