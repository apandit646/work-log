"""Plain data classes shared by builder.py, draft.py, and render.py.

Kept in their own module (rather than living in builder.py) so draft.py
can type-hint against them without builder.py <-> draft.py becoming a
circular import.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
from typing import Dict, List, Optional


@dataclasses.dataclass
class ActivityBlockInfo:
    app: str
    title: str
    category: str
    start: _dt.datetime
    end: _dt.datetime

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


@dataclasses.dataclass
class MeetingInfo:
    title: str
    start: _dt.datetime
    end: _dt.datetime
    calendar_source: Optional[str]
    all_day: bool = False

    @property
    def minutes(self) -> float:
        return (self.end - self.start).total_seconds() / 60.0


@dataclasses.dataclass
class CommitInfo:
    hash: str
    subject: str
    branch: Optional[str]
    timestamp: str
    additions: int
    deletions: int


@dataclasses.dataclass
class RepoCommits:
    repo: str
    commits: List[CommitInfo]

    @property
    def additions(self) -> int:
        return sum(c.additions for c in self.commits)

    @property
    def deletions(self) -> int:
        return sum(c.deletions for c in self.commits)


@dataclasses.dataclass
class RepoWip:
    repo: str
    files: List[Dict[str, str]]


@dataclasses.dataclass
class Report:
    day: str
    status: str  # draft | ready | submitted
    generated_at: str

    category_totals: List["tuple[str, float]"]  # [(category, minutes), ...] desc by minutes
    total_tracked_minutes: float
    top_windows: List["tuple[str, str, float]"]  # [(app, title, minutes), ...] desc

    meetings: List[MeetingInfo]
    commits_by_repo: List[RepoCommits]
    wip_by_repo: List[RepoWip]
    draft_lines: List[str]

    timeline: List[ActivityBlockInfo]

    git_available: bool
    git_error: Optional[str]
    calendar_available: bool
    calendar_error: Optional[str]
