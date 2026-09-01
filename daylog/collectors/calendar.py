"""Calendar collector: fetches one or more private iCal (.ics) feeds over
HTTPS and expands events (including recurring ones) for a requested day.

Outlook/Microsoft 365 and Google Calendar both expose the same kind of
private "secret address" iCal URL, so one code path covers both — no
OAuth, no provider-specific API.

Parsing uses the `icalendar` package (RFC 5545 is not something to
hand-parse) and its RRULE expansion (`python-dateutil` underneath) — see
pyproject.toml for why these are the one exception to the stdlib-only
core. Nothing here raises for an ordinary failure: an unreachable URL, a
malformed feed, or one bad VEVENT among many all degrade gracefully
(source marked unavailable, or that one VEVENT skipped) rather than
taking down the whole report.

Scope note: an individually modified or cancelled *single occurrence* of
a recurring event (a VEVENT with RECURRENCE-ID) is not cross-referenced
against the series — it's treated as its own event based on its own
DTSTART. In the common case (STATUS:CANCELLED on the override, or the
occurrence rescheduled to a different day) this is harmless; the one gap
is a single occurrence declined-in-place, which may still appear once at
its original series time. Full override reconciliation was judged not
worth the added complexity for this tool.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import hashlib
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple

from dateutil.rrule import rrulestr
from icalendar import Calendar

from ..paths import data_dir

if TYPE_CHECKING:
    from ..config import Config

_TIMEOUT = 15


@dataclasses.dataclass
class MeetingEvent:
    uid: str
    title: str
    start: _dt.datetime  # tz-aware
    end: _dt.datetime  # tz-aware
    all_day: bool
    calendar_source: str

    @property
    def duration_minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0

    def to_cache_row(self) -> Dict[str, Any]:
        return {
            "uid": self.uid,
            "title": self.title,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "calendar_source": self.calendar_source,
            "all_day": self.all_day,
        }


@dataclasses.dataclass
class CalendarSourceResult:
    url: str
    available: bool
    error: Optional[str] = None
    events: List[MeetingEvent] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class CalendarCollection:
    sources: List[CalendarSourceResult] = dataclasses.field(default_factory=list)

    @property
    def available(self) -> bool:
        """No sources configured is not a failure — 'unavailable' means a
        configured source couldn't be reached."""
        return not self.sources or any(s.available for s in self.sources)

    @property
    def events(self) -> List[MeetingEvent]:
        return sorted((e for s in self.sources for e in s.events), key=lambda e: e.start)


def meetings_to_cache_rows(collection: CalendarCollection) -> List[Dict[str, Any]]:
    """Shape expected by storage.replace_meetings_cache()."""
    return [e.to_cache_row() for e in collection.events]


# --- fetching + caching ----------------------------------------------------


def _cache_path(url: str) -> Path:
    digest = hashlib.sha256(url.encode("utf-8")).hexdigest()[:16]
    return data_dir() / "cache" / "calendar" / f"{digest}.ics"


def _read_cache(cache_file: Path) -> Optional[str]:
    try:
        return cache_file.read_text(encoding="utf-8")
    except OSError:
        return None


def fetch_ics(url: str, cache_minutes: int) -> Tuple[Optional[str], Optional[str]]:
    """Returns (ics_text, error). Serves a local cache within
    `cache_minutes` so repeated report runs don't re-download, and falls
    back to a stale cache on a network failure rather than losing
    yesterday's data over a flaky connection."""
    cache_file = _cache_path(url)
    if cache_file.exists():
        age_seconds = time.time() - cache_file.stat().st_mtime
        if age_seconds < cache_minutes * 60:
            cached = _read_cache(cache_file)
            if cached is not None:
                return cached, None

    try:
        request = urllib.request.Request(url, headers={"User-Agent": "daylog/1.0"})
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:
            raw = response.read()
    except (urllib.error.URLError, OSError, ValueError) as exc:
        stale = _read_cache(cache_file)
        if stale is not None:
            return stale, None
        return None, f"could not fetch calendar: {exc}"

    text = raw.decode("utf-8", errors="replace")
    try:
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(text, encoding="utf-8")
    except OSError:
        pass  # caching is an optimization, not a requirement
    return text, None


def _label_for_url(url: str, index: int) -> str:
    return urllib.parse.urlparse(url).hostname or f"calendar-{index + 1}"


# --- parsing + recurrence expansion -----------------------------------------


def _event_bounds(component: Any) -> Tuple[_dt.datetime, _dt.datetime, bool]:
    """Normalizes DTSTART/DTEND to tz-aware datetimes. All-day (date-only)
    values become midnight-to-midnight in the local timezone."""
    start_val = component.get("dtstart").dt
    all_day = not isinstance(start_val, _dt.datetime)

    if all_day:
        start = _dt.datetime.combine(start_val, _dt.time.min).astimezone()
        dtend = component.get("dtend")
        end_date = dtend.dt if dtend is not None else start_val + _dt.timedelta(days=1)
        end = _dt.datetime.combine(end_date, _dt.time.min).astimezone()
        return start, end, True

    start = start_val if start_val.tzinfo else start_val.replace(tzinfo=_dt.timezone.utc)
    dtend = component.get("dtend")
    if dtend is not None:
        end_val = dtend.dt
        end = end_val if end_val.tzinfo else end_val.replace(tzinfo=_dt.timezone.utc)
    else:
        duration = component.get("duration")
        end = start + duration.dt if duration is not None else start
    return start, end, False


def _spans_day(start: _dt.datetime, end: _dt.datetime, day_start: _dt.datetime, day_end: _dt.datetime) -> bool:
    return start < day_end and end > day_start


def _is_declined(component: Any, owner_email: str) -> bool:
    status = component.get("status")
    if status is not None and str(status).upper() == "CANCELLED":
        return True
    if not owner_email:
        return False
    attendees = component.get("attendee")
    if attendees is None:
        return False
    if not isinstance(attendees, list):
        attendees = [attendees]
    for attendee in attendees:
        email = str(attendee).replace("mailto:", "").replace("MAILTO:", "").strip().lower()
        if email != owner_email.lower():
            continue
        partstat = attendee.params.get("PARTSTAT", "")
        if str(partstat).upper() == "DECLINED":
            return True
    return False


def _collect_exdates(component: Any) -> Set[_dt.datetime]:
    exdates: Set[_dt.datetime] = set()
    raw = component.get("exdate")
    if raw is None:
        return exdates
    items = raw if isinstance(raw, list) else [raw]
    for item in items:
        for dt_prop in item.dts:
            value = dt_prop.dt
            if isinstance(value, _dt.datetime):
                exdates.add(value if value.tzinfo else value.replace(tzinfo=_dt.timezone.utc))
            else:
                exdates.add(value)
    return exdates


def _expand_rrule(
    rrule_prop: Any,
    dtstart: _dt.datetime,
    day_start: _dt.datetime,
    day_end: _dt.datetime,
    exdates: Set[_dt.datetime],
) -> List[_dt.datetime]:
    rule_text = rrule_prop.to_ical().decode("utf-8")
    try:
        rule = rrulestr(f"RRULE:{rule_text}", dtstart=dtstart)
    except (ValueError, TypeError):
        return []

    # A couple of days' padding either side: an occurrence generated in
    # dtstart's own timezone can still land inside [day_start, day_end)
    # once compared as an absolute instant.
    window_start = day_start - _dt.timedelta(days=2)
    window_end = day_end + _dt.timedelta(days=2)
    occurrences = []
    for occ in rule.between(window_start, window_end, inc=True):
        if occ in exdates:
            continue
        if day_start <= occ < day_end:
            occurrences.append(occ)
    return occurrences


def events_from_ics(
    ics_text: str, day: str, owner_email: str = "", source_label: str = "calendar"
) -> List[MeetingEvent]:
    """Parses one .ics feed and returns every event (expanding recurrences)
    that falls on `day`, skipping declined events. Never raises: a feed
    that fails to parse at all yields an empty list; one malformed VEVENT
    is skipped and parsing continues."""
    day_date = _dt.date.fromisoformat(day)
    day_start = _dt.datetime.combine(day_date, _dt.time.min).astimezone()
    day_end = day_start + _dt.timedelta(days=1)

    try:
        cal = Calendar.from_ical(ics_text)
    except (ValueError, IndexError):
        return []

    events: List[MeetingEvent] = []
    for component in cal.walk("VEVENT"):
        uid = str(component.get("uid", "")).strip()
        if not uid:
            continue
        try:
            start, end, all_day = _event_bounds(component)
        except Exception:
            continue  # one malformed VEVENT must not break the whole feed

        if _is_declined(component, owner_email):
            continue

        title = str(component.get("summary", "")).strip() or "Untitled"
        duration = end - start
        rrule_prop = component.get("rrule")

        if rrule_prop is None:
            if _spans_day(start, end, day_start, day_end):
                events.append(MeetingEvent(uid, title, start, end, all_day, source_label))
            continue

        exdates = _collect_exdates(component)
        for occ_start in _expand_rrule(rrule_prop, start, day_start, day_end, exdates):
            occ_end = occ_start + duration
            events.append(
                MeetingEvent(
                    f"{uid}:{occ_start.isoformat()}", title, occ_start, occ_end, all_day, source_label
                )
            )

    return events


def collect(config: "Config", day: str) -> CalendarCollection:
    sources: List[CalendarSourceResult] = []
    for index, url in enumerate(config.calendar.ics_urls):
        label = _label_for_url(url, index)
        ics_text, error = fetch_ics(url, config.calendar.cache_minutes)
        if ics_text is None:
            sources.append(CalendarSourceResult(url=url, available=False, error=error))
            continue
        events = events_from_ics(ics_text, day, config.calendar.owner_email, label)
        events.sort(key=lambda e: e.start)
        sources.append(CalendarSourceResult(url=url, available=True, events=events))
    return CalendarCollection(sources=sources)
