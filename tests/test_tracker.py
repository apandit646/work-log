import datetime as dt

import pytest

from daylog import storage
from daylog.collectors.window import WindowSample
from daylog.config import default_config
from daylog.tracker import Tracker


@pytest.fixture
def conn(daylog_home):
    with storage.open_db() as c:
        yield c


class _Inputs:
    """Test double for the tracker's (active_window, idle_seconds, now)
    callables — set the attributes before each poll_once() call."""

    def __init__(self):
        self.now = None
        self.idle = 0.0
        self.sample = None

    def get_now(self):
        return self.now

    def get_idle(self):
        return self.idle

    def get_sample(self):
        return self.sample


def _make_tracker(conn, fx, cfg=None):
    return Tracker(
        conn,
        cfg or default_config(),
        active_window=fx.get_sample,
        idle_seconds=fx.get_idle,
        now=fx.get_now,
    )


def test_consecutive_same_window_samples_merge_into_one_block(conn):
    fx = _Inputs()
    tracker = _make_tracker(conn, fx)

    t0 = dt.datetime(2026, 9, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
    fx.now, fx.sample = t0, WindowSample("Code", "main.py - project")
    tracker.poll_once()

    fx.now = t0 + dt.timedelta(seconds=5)
    tracker.poll_once()

    blocks = storage.get_activity_blocks(conn, "2026-09-01")
    assert len(blocks) == 1
    assert blocks[0]["app"] == "Code"
    assert blocks[0]["category"] == "Coding"
    assert blocks[0]["start"] == t0.isoformat(timespec="seconds")
    assert blocks[0]["end"] == fx.now.isoformat(timespec="seconds")


def test_window_change_closes_old_block_and_opens_new_one(conn):
    fx = _Inputs()
    tracker = _make_tracker(conn, fx)

    t0 = dt.datetime(2026, 9, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
    fx.now, fx.sample = t0, WindowSample("Code", "main.py")
    tracker.poll_once()
    fx.now = t0 + dt.timedelta(seconds=5)
    tracker.poll_once()

    fx.now = t0 + dt.timedelta(seconds=10)
    fx.sample = WindowSample("Chrome", "GitHub - Pull Request")
    tracker.poll_once()

    blocks = storage.get_activity_blocks(conn, "2026-09-01")
    assert len(blocks) == 2
    # The first block stopped growing the moment the window changed — its
    # end is the *last time it was actually seen*, not the change time.
    assert blocks[0]["end"] == (t0 + dt.timedelta(seconds=5)).isoformat(timespec="seconds")
    assert blocks[1]["app"] == "Chrome"
    assert blocks[1]["start"] == blocks[1]["end"] == (t0 + dt.timedelta(seconds=10)).isoformat(timespec="seconds")


def test_open_block_end_is_updated_on_every_matching_poll(conn):
    """The row in the DB is kept continuously current — proof that a kill
    between polls loses at most one poll_interval, never the whole block."""
    fx = _Inputs()
    tracker = _make_tracker(conn, fx)

    t0 = dt.datetime(2026, 9, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
    fx.sample = WindowSample("Code", "main.py")

    for offset in (0, 5, 10, 15):
        fx.now = t0 + dt.timedelta(seconds=offset)
        tracker.poll_once()
        blocks = storage.get_activity_blocks(conn, "2026-09-01")
        assert len(blocks) == 1
        assert blocks[0]["end"] == fx.now.isoformat(timespec="seconds")


def test_idle_beyond_threshold_closes_block_and_records_no_gap(conn):
    cfg = default_config()
    cfg.tracking.idle_threshold_seconds = 60
    fx = _Inputs()
    tracker = _make_tracker(conn, fx, cfg)

    t0 = dt.datetime(2026, 9, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
    fx.now, fx.idle, fx.sample = t0, 0.0, WindowSample("Code", "editing")
    tracker.poll_once()

    fx.now, fx.idle = t0 + dt.timedelta(seconds=90), 90.0
    tracker.poll_once()

    fx.now, fx.idle = t0 + dt.timedelta(seconds=95), 95.0
    tracker.poll_once()  # still idle — nothing new should appear

    fx.now, fx.idle = t0 + dt.timedelta(seconds=200), 0.0
    fx.sample = WindowSample("Code", "editing")  # same app/title as before going idle
    tracker.poll_once()

    blocks = storage.get_activity_blocks(conn, "2026-09-01")
    assert len(blocks) == 2  # not merged across the idle gap
    assert blocks[0]["end"] == t0.isoformat(timespec="seconds")
    assert blocks[1]["start"] == (t0 + dt.timedelta(seconds=200)).isoformat(timespec="seconds")


def test_no_active_window_closes_block_without_crashing(conn):
    fx = _Inputs()
    tracker = _make_tracker(conn, fx)

    t0 = dt.datetime(2026, 9, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
    fx.now, fx.sample = t0, WindowSample("Code", "editing")
    tracker.poll_once()

    fx.now, fx.sample = t0 + dt.timedelta(seconds=5), None
    tracker.poll_once()

    fx.now, fx.sample = t0 + dt.timedelta(seconds=10), WindowSample("Code", "editing")
    tracker.poll_once()

    blocks = storage.get_activity_blocks(conn, "2026-09-01")
    assert len(blocks) == 2


def test_block_never_spans_a_day_boundary(conn):
    fx = _Inputs()
    tracker = _make_tracker(conn, fx)

    before_midnight = dt.datetime(2026, 9, 1, 23, 59, 58, tzinfo=dt.timezone.utc)
    fx.now, fx.sample = before_midnight, WindowSample("Code", "editing")
    tracker.poll_once()

    after_midnight = dt.datetime(2026, 9, 2, 0, 0, 2, tzinfo=dt.timezone.utc)
    fx.now = after_midnight
    tracker.poll_once()

    day1 = storage.get_activity_blocks(conn, "2026-09-01")
    day2 = storage.get_activity_blocks(conn, "2026-09-02")
    assert len(day1) == 1 and len(day2) == 1
    assert day1[0]["end"] == before_midnight.isoformat(timespec="seconds")
    assert day2[0]["start"] == after_midnight.isoformat(timespec="seconds")


def test_run_forever_stops_when_requested(conn, monkeypatch):
    fx = _Inputs()
    fx.now = dt.datetime(2026, 9, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
    fx.sample = WindowSample("Code", "editing")
    tracker = _make_tracker(conn, fx)

    monkeypatch.setattr("daylog.tracker.time.sleep", lambda seconds: None)

    calls = {"n": 0}

    def stop():
        calls["n"] += 1
        return calls["n"] > 3

    tracker.run_forever(stop=stop)
    # The exact call count is an implementation detail (run_forever checks
    # stop() repeatedly in short increments so it responds promptly — see
    # its docstring); what matters is it actually stopped, and poll_once()
    # ran (exactly once, since one unchanging sample only ever extends the
    # same block regardless of how many times it's polled).
    assert calls["n"] >= 4
    assert len(storage.get_activity_blocks(conn, "2026-09-01")) == 1


def test_run_forever_responds_promptly_even_with_a_long_poll_interval(conn, monkeypatch):
    """Regression test for the PEP-475 sleep-doesn't-shorten issue: a
    single time.sleep(interval) would make stop() take a full poll
    interval to take effect. Using the real clock (no mocking) with a
    5-minute interval and a stop flag set from another thread proves the
    loop actually exits promptly instead of sleeping the full interval."""
    import threading
    import time as real_time

    fx = _Inputs()
    fx.now = dt.datetime(2026, 9, 1, 9, 0, 0, tzinfo=dt.timezone.utc)
    fx.sample = WindowSample("Code", "editing")
    cfg = default_config()
    cfg.tracking.poll_interval_seconds = 300  # 5 minutes
    tracker = _make_tracker(conn, fx, cfg)

    stop_flag = {"stop": False}

    def stop():
        return stop_flag["stop"]

    def flip_stop_soon():
        real_time.sleep(0.3)
        stop_flag["stop"] = True

    threading.Thread(target=flip_stop_soon).start()

    start = real_time.time()
    tracker.run_forever(stop=stop)
    elapsed = real_time.time() - start

    assert elapsed < 2.0  # nowhere near the 300s interval
