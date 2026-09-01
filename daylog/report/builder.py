"""Builds a Report from stored data, and refreshes the git/calendar caches
that back it.

Three entry points, deliberately kept separate:

- refresh_day() re-runs the git and calendar collectors and writes their
  results into storage's per-day caches, returning whether each collector
  actually succeeded. This is what "generating"/"regenerating" a day
  means — call it explicitly, never implicitly.
- load_report() is pure read: it only looks at what's already in storage
  (activity_blocks, commits_cache, wip_cache, meetings_cache,
  day_summaries) and aggregates it into a Report, defaulting to
  "available" since a read-only view has no way to know a collector is
  currently broken. This is what makes an old day's report still render
  correctly after a repo is deleted or a meeting disappears from the
  calendar — viewing a day never re-collects.
- generate_report() is refresh_day() + load_report(), with the just-
  collected availability overlaid onto the Report so its accurate for
  the day being actively generated. This is what `daylog report` calls.

The future JSON API (Phase 6) calls load_report() alone for GET, and
generate_report() only for the explicit POST regenerate endpoint.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
from typing import TYPE_CHECKING, Dict, List, Optional

from .. import storage
from ..collectors import calendar as calendar_collector
from ..collectors import git as git_collector
from . import draft as draft_module
from .types import ActivityBlockInfo, CommitInfo, MeetingInfo, Report, RepoCommits, RepoWip

if TYPE_CHECKING:
    import sqlite3

    from ..config import Config

_TOP_WINDOWS_LIMIT = 10


@dataclasses.dataclass
class RefreshResult:
    git_available: bool
    git_error: Optional[str]
    calendar_available: bool
    calendar_error: Optional[str]


def refresh_day(config: "Config", conn: "sqlite3.Connection", day: str) -> RefreshResult:
    """Re-collects git and calendar data for `day` and replaces the cached
    rows for it. Leaves the cache untouched for a source that failed to
    collect (git missing, calendar unreachable) — stale data beats none."""
    git_result = git_collector.collect(config, day)
    if git_result.available:
        storage.replace_commits_cache(conn, day, git_collector.commits_to_cache_rows(git_result))
        storage.replace_wip_cache(conn, day, git_collector.wip_to_cache_rows(git_result))

    cal_result = calendar_collector.collect(config, day)
    if cal_result.available:
        storage.replace_meetings_cache(conn, day, calendar_collector.meetings_to_cache_rows(cal_result))

    cal_error = None
    if not cal_result.available:
        cal_error = "; ".join(f"{s.url}: {s.error}" for s in cal_result.sources if s.error) or None

    return RefreshResult(
        git_available=git_result.available,
        git_error=git_result.error,
        calendar_available=cal_result.available,
        calendar_error=cal_error,
    )


def load_report(conn: "sqlite3.Connection", day: str) -> Report:
    """Builds a Report purely from what's already in storage — never
    touches git, the network, or the config's collector settings.
    git_available/calendar_available default to True: a read-only view
    has no way to know a collector is currently broken, and that's fine —
    it just means an old cached day renders as if nothing is wrong, which
    is exactly the point of caching in the first place."""
    blocks = _load_activity_blocks(conn, day)
    category_totals = _category_totals(blocks)
    total_minutes = sum(b.minutes for b in blocks)
    top_windows = _top_windows(blocks)

    commits_by_repo = _load_commits_by_repo(conn, day)
    wip_by_repo = [RepoWip(repo=w["repo"], files=w["files"]) for w in storage.get_wip(conn, day)]
    meetings = _load_meetings(conn, day)

    draft_lines = draft_module.build_draft(commits_by_repo, meetings)

    existing_summary = storage.get_day_summary(conn, day)
    status = existing_summary.status if existing_summary else "draft"

    return Report(
        day=day,
        status=status,
        generated_at=_dt.datetime.now().astimezone().isoformat(timespec="seconds"),
        category_totals=category_totals,
        total_tracked_minutes=total_minutes,
        top_windows=top_windows,
        meetings=meetings,
        commits_by_repo=commits_by_repo,
        wip_by_repo=wip_by_repo,
        draft_lines=draft_lines,
        timeline=blocks,
        git_available=True,
        git_error=None,
        calendar_available=True,
        calendar_error=None,
    )


def generate_report(config: "Config", conn: "sqlite3.Connection", day: str) -> Report:
    """refresh_day() + load_report(), with the just-collected availability
    overlaid so the result accurately reflects this run, not a guess."""
    refresh = refresh_day(config, conn, day)
    report = load_report(conn, day)
    report.git_available = refresh.git_available
    report.git_error = refresh.git_error
    report.calendar_available = refresh.calendar_available
    report.calendar_error = refresh.calendar_error
    return report


def _load_activity_blocks(conn: "sqlite3.Connection", day: str) -> List[ActivityBlockInfo]:
    rows = storage.get_activity_blocks(conn, day)
    blocks = []
    for row in rows:
        try:
            start = _dt.datetime.fromisoformat(row["start"])
            end = _dt.datetime.fromisoformat(row["end"])
        except ValueError:
            continue
        blocks.append(
            ActivityBlockInfo(app=row["app"], title=row["title"], category=row["category"], start=start, end=end)
        )
    return blocks


def _category_totals(blocks: List[ActivityBlockInfo]) -> "List[tuple[str, float]]":
    totals: Dict[str, float] = {}
    for block in blocks:
        totals[block.category] = totals.get(block.category, 0.0) + block.minutes
    return sorted(totals.items(), key=lambda item: item[1], reverse=True)


def _top_windows(blocks: List[ActivityBlockInfo]) -> "List[tuple[str, str, float]]":
    totals: Dict["tuple[str, str]", float] = {}
    for block in blocks:
        key = (block.app, block.title)
        totals[key] = totals.get(key, 0.0) + block.minutes
    ranked = sorted(totals.items(), key=lambda item: item[1], reverse=True)
    return [(app, title, minutes) for (app, title), minutes in ranked[:_TOP_WINDOWS_LIMIT]]


def _load_commits_by_repo(conn: "sqlite3.Connection", day: str) -> List[RepoCommits]:
    by_repo: Dict[str, List[CommitInfo]] = {}
    order: List[str] = []
    for row in storage.get_commits(conn, day):
        repo = row["repo"]
        if repo not in by_repo:
            by_repo[repo] = []
            order.append(repo)
        by_repo[repo].append(
            CommitInfo(
                hash=row["hash"],
                subject=row["subject"],
                branch=row["branch"],
                timestamp=row["timestamp"],
                additions=row["additions"],
                deletions=row["deletions"],
            )
        )
    return [RepoCommits(repo=repo, commits=by_repo[repo]) for repo in order]


def _load_meetings(conn: "sqlite3.Connection", day: str) -> List[MeetingInfo]:
    meetings = []
    for row in storage.get_meetings(conn, day):
        try:
            start = _dt.datetime.fromisoformat(row["start"])
            end = _dt.datetime.fromisoformat(row["end"])
        except ValueError:
            continue
        meetings.append(
            MeetingInfo(
                title=row["title"],
                start=start,
                end=end,
                calendar_source=row["calendar_source"],
                all_day=bool(row["all_day"]),
            )
        )
    meetings.sort(key=lambda m: m.start)
    return meetings
