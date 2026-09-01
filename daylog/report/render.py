"""Report -> Markdown (and -> a JSON-serializable dict). Pure formatting —
no aggregation happens here; everything it needs is already on the Report
object from builder.py.
"""
from __future__ import annotations

import dataclasses
from typing import Any, Dict

from .types import Report

_BAR_WIDTH = 28


def _fmt_minutes(minutes: float) -> str:
    total = round(minutes)
    hours, mins = divmod(total, 60)
    if hours and mins:
        return f"{hours}h {mins}m"
    if hours:
        return f"{hours}h"
    return f"{mins}m"


def _bar(minutes: float, max_minutes: float) -> str:
    if max_minutes <= 0:
        filled = 0
    else:
        filled = round((minutes / max_minutes) * _BAR_WIDTH)
    filled = max(0, min(_BAR_WIDTH, filled))
    return "█" * filled + "░" * (_BAR_WIDTH - filled)


def render_markdown(report: Report) -> str:
    lines = [f"# Daylog — {report.day}", ""]
    lines.append(f"**Status:** {report.status}")
    lines.append(f"**Generated:** {report.generated_at}")
    lines.append("")

    if not report.git_available and report.git_error:
        lines.append(f"> ⚠️ Git data unavailable: {report.git_error}")
        lines.append("")
    if not report.calendar_available and report.calendar_error:
        lines.append(f"> ⚠️ Calendar data unavailable: {report.calendar_error}")
        lines.append("")

    lines.append("## Time by category")
    lines.append("")
    if report.category_totals:
        max_minutes = max(m for _, m in report.category_totals)
        width = max(len(cat) for cat, _ in report.category_totals)
        for category, minutes in report.category_totals:
            bar = _bar(minutes, max_minutes)
            lines.append(f"{category.ljust(width)}  {bar}  {_fmt_minutes(minutes)}")
    else:
        lines.append("_No activity recorded._")
    lines.append("")
    lines.append(f"**Total tracked:** {_fmt_minutes(report.total_tracked_minutes)}")
    lines.append("")

    lines.append("## Meetings")
    lines.append("")
    if report.meetings:
        for meeting in report.meetings:
            source = f" ({meeting.calendar_source})" if meeting.calendar_source else ""
            if meeting.all_day:
                lines.append(f"- {meeting.title} (all day){source}")
            else:
                start = meeting.start.strftime("%H:%M")
                end = meeting.end.strftime("%H:%M")
                lines.append(f"- {start}–{end} {meeting.title}{source}")
    else:
        lines.append("_No meetings today._")
    lines.append("")

    lines.append("## Commits")
    lines.append("")
    if report.commits_by_repo:
        for repo_commits in report.commits_by_repo:
            lines.append(
                f"### {repo_commits.repo} "
                f"(+{repo_commits.additions}/-{repo_commits.deletions}, "
                f"{len(repo_commits.commits)} commit{'s' if len(repo_commits.commits) != 1 else ''})"
            )
            for commit in repo_commits.commits:
                branch = f" ({commit.branch})" if commit.branch else ""
                lines.append(f"- `{commit.hash[:7]}` {commit.subject}{branch}")
            lines.append("")
    else:
        lines.append("_No commits today._")
        lines.append("")

    lines.append("## Work in progress")
    lines.append("")
    if report.wip_by_repo:
        for repo_wip in report.wip_by_repo:
            lines.append(f"### {repo_wip.repo}")
            for f in repo_wip.files:
                lines.append(f"- `{f['status']}` {f['path']}")
            lines.append("")
    else:
        lines.append("_Nothing uncommitted._")
        lines.append("")

    lines.append("## Top windows")
    lines.append("")
    if report.top_windows:
        for app, title, minutes in report.top_windows:
            label = f"{app} — {title}" if title else app
            lines.append(f"- {label}: {_fmt_minutes(minutes)}")
    else:
        lines.append("_No window activity recorded._")
    lines.append("")

    lines.append("## Draft for the timesheet")
    lines.append("")
    if report.draft_lines:
        for line in report.draft_lines:
            lines.append(f"- {line}")
    else:
        lines.append("_Nothing to report yet._")
    lines.append("")

    return "\n".join(lines)


def render_draft_text(report: Report) -> str:
    """Just the "Draft for the timesheet" bullets, as plain Markdown list
    text — this is what actually gets pasted into an office form, and
    what day_summaries.generated_md stores (not the full multi-section
    report, which render_markdown() produces for on-screen/--out display
    only). Empty string if there's no evidence-backed work to report."""
    return "\n".join(f"- {line}" for line in report.draft_lines)


def render_json(report: Report) -> Dict[str, Any]:
    return {
        "day": report.day,
        "status": report.status,
        "generated_at": report.generated_at,
        "category_totals": [{"category": c, "minutes": round(m, 1)} for c, m in report.category_totals],
        "total_tracked_minutes": round(report.total_tracked_minutes, 1),
        "top_windows": [
            {"app": app, "title": title, "minutes": round(m, 1)} for app, title, m in report.top_windows
        ],
        "meetings": [
            {
                "title": m.title,
                "start": m.start.isoformat(),
                "end": m.end.isoformat(),
                "minutes": round(m.minutes, 1),
                "calendar_source": m.calendar_source,
                "all_day": m.all_day,
            }
            for m in report.meetings
        ],
        "commits_by_repo": [
            {
                "repo": rc.repo,
                "additions": rc.additions,
                "deletions": rc.deletions,
                "commits": [dataclasses.asdict(c) for c in rc.commits],
            }
            for rc in report.commits_by_repo
        ],
        "wip_by_repo": [{"repo": rw.repo, "files": rw.files} for rw in report.wip_by_repo],
        "draft_lines": report.draft_lines,
        "timeline": [
            {
                "app": b.app,
                "title": b.title,
                "category": b.category,
                "start": b.start.isoformat(),
                "end": b.end.isoformat(),
            }
            for b in report.timeline
        ],
        "git_available": report.git_available,
        "git_error": report.git_error,
        "calendar_available": report.calendar_available,
        "calendar_error": report.calendar_error,
    }
