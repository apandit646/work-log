import time

from daylog import pidfile, tracker_process
from daylog.cli import main


def _wait_until_running(timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pidfile.tracker_status()[0]:
            return True
        time.sleep(0.05)
    return False


def test_start_tracker_spawns_and_writes_pidfile(daylog_home):
    main(["init"])
    pid = tracker_process.start_tracker()
    try:
        assert _wait_until_running()
        running, running_pid = pidfile.tracker_status()
        assert running is True
        assert running_pid == pid
    finally:
        tracker_process.stop_tracker()


def test_start_tracker_is_a_noop_when_already_running(daylog_home):
    main(["init"])
    first_pid = tracker_process.start_tracker()
    try:
        assert _wait_until_running()
        second_pid = tracker_process.start_tracker()
        assert second_pid == first_pid
    finally:
        tracker_process.stop_tracker()


def test_stop_tracker_gracefully_stops_a_running_tracker(daylog_home):
    main(["init"])
    tracker_process.start_tracker()
    assert _wait_until_running()

    stopped = tracker_process.stop_tracker(timeout=5.0)
    assert stopped is True
    assert pidfile.tracker_status() == (False, None)


def test_stop_tracker_when_not_running_is_a_noop(daylog_home):
    assert tracker_process.stop_tracker() is True
