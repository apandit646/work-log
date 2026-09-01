"""Polls the active window and turns consecutive same-window samples into
a single activity_blocks row, extending its `end` column on every matching
poll rather than only writing once a block finishes.

That's what makes tracking kill-safe: at any instant the database already
reflects the last successful poll, so a sleep/reboot/kill loses at most one
poll_interval_seconds of the block that was open at the time, and a fresh
start afterwards just begins a new block — nothing is re-recorded and
nothing already written is touched.
"""
from __future__ import annotations

import datetime as _dt
import time
from dataclasses import dataclass
from typing import Callable, Optional

from . import storage
from .categorize import categorize
from .collectors import window as window_collector
from .config import Config


def _default_now() -> _dt.datetime:
    return _dt.datetime.now().astimezone()


def _iso(moment: _dt.datetime) -> str:
    return moment.isoformat(timespec="seconds")


def _day(moment: _dt.datetime) -> str:
    return moment.strftime("%Y-%m-%d")


@dataclass
class _OpenBlock:
    block_id: int
    app: str
    title: str
    day: str


class Tracker:
    def __init__(
        self,
        conn,
        config: Config,
        active_window: Callable[[], Optional[window_collector.WindowSample]] = window_collector.active_window,
        idle_seconds: Callable[[], float] = window_collector.idle_seconds,
        now: Callable[[], _dt.datetime] = _default_now,
    ) -> None:
        self._conn = conn
        self._config = config
        self._active_window = active_window
        self._idle_seconds = idle_seconds
        self._now = now
        self._open: Optional[_OpenBlock] = None

    def poll_once(self) -> None:
        now = self._now()

        if self._idle_seconds() >= self._config.tracking.idle_threshold_seconds:
            self._open = None
            return

        sample = self._active_window()
        if sample is None:
            self._open = None
            return

        day = _day(now)
        if (
            self._open is not None
            and self._open.app == sample.app
            and self._open.title == sample.title
            and self._open.day == day
        ):
            storage.update_activity_block_end(self._conn, self._open.block_id, _iso(now))
            return

        category = categorize(sample.app, sample.title, self._config.categories)
        block_id = storage.insert_activity_block(
            self._conn, day, _iso(now), _iso(now), sample.app, sample.title, category
        )
        self._open = _OpenBlock(block_id, sample.app, sample.title, day)

    def run_forever(self, stop: Callable[[], bool] = lambda: False) -> None:
        interval = self._config.tracking.poll_interval_seconds
        while not stop():
            self.poll_once()
            if stop():
                break
            time.sleep(interval)
