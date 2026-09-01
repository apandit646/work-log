import datetime as dt

from daylog.report import render
from daylog.report.types import (
    ActivityBlockInfo,
    CommitInfo,
    MeetingInfo,
    Report,
    RepoCommits,
    RepoWip,
)


def _empty_report(**overrides):
    base = dict(
        day="2026-09-01",
        status="draft",
        generated_at="2026-09-01T18:00:00+00:00",
        category_totals=[],
        total_tracked_minutes=0.0,
        top_windows=[],
        meetings=[],
        commits_by_repo=[],
        wip_by_repo=[],
        draft_lines=[],
        timeline=[],
        git_available=True,
        git_error=None,
        calendar_available=True,
        calendar_error=None,
        llm_used=False,
        llm_error=None,
    )
    base.update(overrides)
    if "draft_text" not in base:
        base["draft_text"] = "\n".join(f"- {line}" for line in base["draft_lines"])
    return Report(**base)


def test_fmt_minutes():
    assert render._fmt_minutes(45) == "45m"
    assert render._fmt_minutes(60) == "1h"
    assert render._fmt_minutes(150) == "2h 30m"
    assert render._fmt_minutes(0) == "0m"


def test_bar_proportions():
    assert render._bar(0, 100) == "░" * render._BAR_WIDTH
    assert render._bar(100, 100) == "█" * render._BAR_WIDTH
    half = render._bar(50, 100)
    assert half.count("█") == render._BAR_WIDTH // 2


def test_bar_handles_zero_max_without_dividing_by_zero():
    assert render._bar(0, 0) == "░" * render._BAR_WIDTH


def test_markdown_empty_day_shows_placeholders_not_crash():
    md = render.render_markdown(_empty_report())
    assert "# Daylog — 2026-09-01" in md
    assert "_No activity recorded._" in md
    assert "_No meetings today._" in md
    assert "_No commits today._" in md
    assert "_Nothing uncommitted._" in md
    assert "_No window activity recorded._" in md
    assert "_Nothing to report yet._" in md


def test_markdown_includes_category_totals_and_bar_chart():
    report = _empty_report(category_totals=[("Coding", 120.0), ("Meetings", 30.0)], total_tracked_minutes=150.0)
    md = render.render_markdown(report)
    assert "Coding" in md and "2h" in md
    assert "Meetings" in md and "30m" in md
    assert "Total tracked:** 2h 30m" in md


def test_markdown_shows_git_and_calendar_warnings_when_unavailable():
    report = _empty_report(git_available=False, git_error="git not found", calendar_available=True)
    md = render.render_markdown(report)
    assert "git not found" in md


def test_markdown_no_warning_when_available():
    report = _empty_report(git_available=True, git_error=None)
    md = render.render_markdown(report)
    assert "unavailable" not in md.lower()


def test_markdown_all_day_meeting_formatted_without_times():
    start = dt.datetime(2026, 9, 1, 0, 0, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(days=1)
    meeting = MeetingInfo(title="Company offsite", start=start, end=end, calendar_source="work", all_day=True)
    md = render.render_markdown(_empty_report(meetings=[meeting]))
    meetings_section = md.split("## Meetings")[1].split("## Commits")[0]
    assert "Company offsite (all day)" in meetings_section
    assert "00:00" not in meetings_section


def test_markdown_timed_meeting_shows_start_and_end():
    start = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.timezone.utc)
    end = start + dt.timedelta(minutes=30)
    meeting = MeetingInfo(title="Standup", start=start, end=end, calendar_source="work")
    md = render.render_markdown(_empty_report(meetings=[meeting]))
    assert "09:00–09:30 Standup" in md


def test_markdown_commits_section_shows_repo_stats_and_hashes():
    commit = CommitInfo(hash="abcdef1234", subject="Fixed a crash", branch="main",
                         timestamp="2026-09-01T09:00:00+00:00", additions=5, deletions=2)
    repo = RepoCommits(repo="invoice-service", commits=[commit])
    md = render.render_markdown(_empty_report(commits_by_repo=[repo]))
    assert "invoice-service (+5/-2, 1 commit)" in md
    assert "`abcdef1`" in md  # short hash


def test_markdown_wip_section_lists_files_with_status():
    wip = RepoWip(repo="invoice-service", files=[{"path": "parser.py", "status": "M"}])
    md = render.render_markdown(_empty_report(wip_by_repo=[wip]))
    assert "`M` parser.py" in md


def test_markdown_top_windows_section():
    md = render.render_markdown(_empty_report(top_windows=[("Code", "main.py", 90.0), ("Chrome", "", 15.0)]))
    assert "Code — main.py: 1h 30m" in md
    assert "Chrome: 15m" in md  # no title -> no dash


def test_markdown_draft_section_lists_bullets():
    md = render.render_markdown(_empty_report(draft_lines=["Fixed a crash in the parser.", "Attended standup."]))
    assert "- Fixed a crash in the parser." in md
    assert "- Attended standup." in md


def test_render_json_roundtrips_key_fields():
    start = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.timezone.utc)
    block = ActivityBlockInfo(app="Code", title="main.py", category="Coding", start=start, end=start + dt.timedelta(minutes=30))
    commit = CommitInfo(hash="abc1234", subject="Fixed a crash", branch="main",
                         timestamp="2026-09-01T09:00:00+00:00", additions=3, deletions=1)
    report = _empty_report(
        category_totals=[("Coding", 30.0)],
        total_tracked_minutes=30.0,
        timeline=[block],
        commits_by_repo=[RepoCommits(repo="proj", commits=[commit])],
        draft_lines=["Fixed a crash in proj."],
    )
    data = render.render_json(report)
    assert data["day"] == "2026-09-01"
    assert data["category_totals"] == [{"category": "Coding", "minutes": 30.0}]
    assert data["timeline"][0]["app"] == "Code"
    assert data["commits_by_repo"][0]["additions"] == 3
    assert data["draft_lines"] == ["Fixed a crash in proj."]

    import json
    json.dumps(data)  # must be fully JSON-serializable
