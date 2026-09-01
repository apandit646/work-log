"""Calendar collector tests. Recurrence/timezone/all-day parsing is tested
against checked-in .ics fixtures with zero network calls; fetch_ics's
caching/error handling is tested with urlopen mocked — also no network."""
import datetime as dt
from pathlib import Path

import pytest

from daylog.collectors import calendar as cal
from daylog.config import default_config

FIXTURES = Path(__file__).parent / "fixtures"


def _read(name):
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- recurrence, all-day, timezone (fixture-based) --------------------------


def test_recurring_weekly_event_expands_on_matching_days():
    ics = _read("recurring_standup.ics")

    on_start_day = cal.events_from_ics(ics, "2026-06-01")
    assert len(on_start_day) == 1
    assert on_start_day[0].title == "Daily standup"

    one_week_later = cal.events_from_ics(ics, "2026-06-08")
    assert len(one_week_later) == 1

    mid_week = cal.events_from_ics(ics, "2026-06-04")
    assert mid_week == []

    before_series_started = cal.events_from_ics(ics, "2026-05-25")
    assert before_series_started == []


def test_recurring_event_uid_is_unique_per_occurrence():
    ics = _read("recurring_standup.ics")
    first = cal.events_from_ics(ics, "2026-06-01")[0]
    second = cal.events_from_ics(ics, "2026-06-08")[0]
    assert first.uid != second.uid


def test_all_day_event_matches_only_its_own_day():
    ics = _read("all_day_event.ics")

    events = cal.events_from_ics(ics, "2026-06-15")
    assert len(events) == 1
    assert events[0].all_day is True
    assert events[0].title == "Company offsite"

    assert cal.events_from_ics(ics, "2026-06-14") == []
    assert cal.events_from_ics(ics, "2026-06-16") == []  # DTEND is exclusive


def test_cross_timezone_meeting_converts_correctly():
    ics = _read("cross_timezone_meeting.ics")

    # This sandbox's local timezone is UTC; 09:30 America/New_York (EDT,
    # UTC-4) on 2026-07-01 is 13:30 UTC the same day.
    events = cal.events_from_ics(ics, "2026-07-01")
    assert len(events) == 1
    event = events[0]
    assert event.start.astimezone(dt.timezone.utc).hour == 13
    assert event.start.astimezone(dt.timezone.utc).minute == 30
    assert event.title == "Cross-timezone client call"

    assert cal.events_from_ics(ics, "2026-06-30") == []
    assert cal.events_from_ics(ics, "2026-07-02") == []


# --- declined events ---------------------------------------------------


def _ics(summary, status=None, attendee_email=None, partstat=None, uid="decl-001@example.com"):
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//daylog test fixture//EN",
        "BEGIN:VEVENT",
        f"UID:{uid}",
        "DTSTAMP:20260101T000000Z",
        "DTSTART:20260901T090000Z",
        "DTEND:20260901T093000Z",
        f"SUMMARY:{summary}",
    ]
    if status:
        lines.append(f"STATUS:{status}")
    if attendee_email:
        params = f";PARTSTAT={partstat}" if partstat else ""
        lines.append(f"ATTENDEE{params}:mailto:{attendee_email}")
    lines += ["END:VEVENT", "END:VCALENDAR", ""]
    return "\n".join(lines)


def test_status_cancelled_event_is_skipped():
    ics = _ics("Cancelled sync", status="CANCELLED")
    assert cal.events_from_ics(ics, "2026-09-01") == []


def test_declined_via_own_partstat_is_skipped():
    ics = _ics(
        "Roadmap review", attendee_email="me@example.com", partstat="DECLINED"
    )
    assert cal.events_from_ics(ics, "2026-09-01", owner_email="me@example.com") == []


def test_accepted_event_with_attendees_is_kept():
    ics = _ics(
        "Roadmap review", attendee_email="me@example.com", partstat="ACCEPTED"
    )
    events = cal.events_from_ics(ics, "2026-09-01", owner_email="me@example.com")
    assert len(events) == 1


def test_someone_elses_decline_does_not_affect_my_view():
    ics = _ics(
        "Roadmap review", attendee_email="other@example.com", partstat="DECLINED"
    )
    events = cal.events_from_ics(ics, "2026-09-01", owner_email="me@example.com")
    assert len(events) == 1


# --- malformed input never raises ---------------------------------------


def test_unparseable_ics_returns_empty_list_not_an_exception():
    assert cal.events_from_ics("not an ics file at all", "2026-09-01") == []


def test_event_missing_dtstart_is_skipped_not_fatal():
    ics = (
        "BEGIN:VCALENDAR\nVERSION:2.0\nBEGIN:VEVENT\nUID:broken@example.com\n"
        "DTSTAMP:20260101T000000Z\nSUMMARY:Missing start\nEND:VEVENT\n"
        "BEGIN:VEVENT\nUID:ok@example.com\nDTSTAMP:20260101T000000Z\n"
        "DTSTART:20260901T090000Z\nDTEND:20260901T093000Z\nSUMMARY:Fine\n"
        "END:VEVENT\nEND:VCALENDAR\n"
    )
    events = cal.events_from_ics(ics, "2026-09-01")
    assert [e.title for e in events] == ["Fine"]


# --- fetch_ics: caching and error handling (network mocked, never real) -----


class _FakeResponse:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def test_fetch_ics_downloads_then_serves_from_cache(daylog_home, monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        return _FakeResponse(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n")

    monkeypatch.setattr(cal.urllib.request, "urlopen", fake_urlopen)

    text1, err1 = cal.fetch_ics("https://example.com/cal.ics", cache_minutes=15)
    text2, err2 = cal.fetch_ics("https://example.com/cal.ics", cache_minutes=15)

    assert err1 is None and err2 is None
    assert "VCALENDAR" in text1 and text1 == text2
    assert calls["n"] == 1  # second call served from the on-disk cache


def test_fetch_ics_refetches_after_cache_expires(daylog_home, monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(request, timeout):
        calls["n"] += 1
        return _FakeResponse(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n")

    monkeypatch.setattr(cal.urllib.request, "urlopen", fake_urlopen)

    cal.fetch_ics("https://example.com/cal.ics", cache_minutes=15)
    cache_file = cal._cache_path("https://example.com/cal.ics")
    old_time = cache_file.stat().st_mtime - 20 * 60  # back-date past the 15-minute window
    import os

    os.utime(cache_file, (old_time, old_time))

    cal.fetch_ics("https://example.com/cal.ics", cache_minutes=15)
    assert calls["n"] == 2


def test_fetch_ics_network_failure_with_no_cache_returns_error(daylog_home, monkeypatch):
    def fake_urlopen(request, timeout):
        raise cal.urllib.error.URLError("connection refused")

    monkeypatch.setattr(cal.urllib.request, "urlopen", fake_urlopen)

    text, error = cal.fetch_ics("https://example.com/cal.ics", cache_minutes=15)
    assert text is None
    assert error is not None and "connection refused" in error


def test_fetch_ics_network_failure_falls_back_to_stale_cache(daylog_home, monkeypatch):
    good_calls = {"n": 0}

    def fake_urlopen_ok(request, timeout):
        good_calls["n"] += 1
        return _FakeResponse(b"BEGIN:VCALENDAR\nEND:VCALENDAR\n")

    monkeypatch.setattr(cal.urllib.request, "urlopen", fake_urlopen_ok)
    cal.fetch_ics("https://example.com/cal.ics", cache_minutes=0)  # write a cache entry

    def fake_urlopen_fail(request, timeout):
        raise cal.urllib.error.URLError("network is down")

    monkeypatch.setattr(cal.urllib.request, "urlopen", fake_urlopen_fail)
    text, error = cal.fetch_ics("https://example.com/cal.ics", cache_minutes=0)

    assert error is None
    assert "VCALENDAR" in text


# --- top-level collect() -------------------------------------------------


def test_collect_with_no_urls_configured_is_available_and_empty():
    result = cal.collect(default_config(), "2026-09-01")
    assert result.available is True
    assert result.events == []


def test_collect_marks_unreachable_source_unavailable(daylog_home, monkeypatch):
    def fake_urlopen(request, timeout):
        raise cal.urllib.error.URLError("no route to host")

    monkeypatch.setattr(cal.urllib.request, "urlopen", fake_urlopen)

    cfg = default_config()
    cfg.calendar.ics_urls = ["https://example.com/cal.ics"]
    result = cal.collect(cfg, "2026-09-01")

    assert result.available is False
    assert result.sources[0].error is not None


def test_meetings_to_cache_rows_shape(daylog_home, monkeypatch):
    def fake_urlopen(request, timeout):
        return _FakeResponse(_read("all_day_event.ics").encode("utf-8"))

    monkeypatch.setattr(cal.urllib.request, "urlopen", fake_urlopen)

    cfg = default_config()
    cfg.calendar.ics_urls = ["https://example.com/cal.ics"]
    result = cal.collect(cfg, "2026-06-15")

    rows = cal.meetings_to_cache_rows(result)
    assert len(rows) == 1
    assert set(rows[0].keys()) == {"uid", "title", "start", "end", "calendar_source"}
