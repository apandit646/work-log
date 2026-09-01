"""Report generation tested against a fixture database with known data,
asserting exact totals — including a day spanning a DST change and a day
with zero activity."""
import datetime as dt

import pytest

from daylog import storage
from daylog.collectors import calendar as calendar_collector
from daylog.collectors import git as git_collector
from daylog.config import default_config
from daylog.report import builder


@pytest.fixture
def conn(daylog_home):
    with storage.open_db() as c:
        yield c


# --- exact category/window totals -------------------------------------


def test_category_totals_are_exact(conn):
    day = "2026-09-01"
    storage.insert_activity_block(conn, day, "2026-09-01T09:00:00+00:00", "2026-09-01T10:05:00+00:00", "Code", "main.py", "Coding")
    storage.insert_activity_block(conn, day, "2026-09-01T10:05:00+00:00", "2026-09-01T10:20:00+00:00", "Code", "utils.py", "Coding")
    storage.insert_activity_block(conn, day, "2026-09-01T11:00:00+00:00", "2026-09-01T11:47:00+00:00", "Teams", "Standup", "Meetings")

    report = builder.load_report(conn, day)

    totals = dict(report.category_totals)
    assert totals["Coding"] == pytest.approx(65 + 15)  # 65m first block + 15m second
    assert totals["Meetings"] == pytest.approx(47)
    assert report.total_tracked_minutes == pytest.approx(65 + 15 + 47)
    # sorted descending by minutes
    assert [c for c, _ in report.category_totals] == ["Coding", "Meetings"]


def test_top_windows_aggregates_across_non_contiguous_blocks(conn):
    day = "2026-09-01"
    storage.insert_activity_block(conn, day, "2026-09-01T09:00:00+00:00", "2026-09-01T09:30:00+00:00", "Code", "main.py", "Coding")
    storage.insert_activity_block(conn, day, "2026-09-01T10:00:00+00:00", "2026-09-01T10:00:00+00:00", "Chrome", "docs", "Browser")
    storage.insert_activity_block(conn, day, "2026-09-01T11:00:00+00:00", "2026-09-01T11:45:00+00:00", "Code", "main.py", "Coding")

    report = builder.load_report(conn, day)

    windows = {(app, title): minutes for app, title, minutes in report.top_windows}
    assert windows[("Code", "main.py")] == pytest.approx(30 + 45)
    assert windows[("Chrome", "docs")] == pytest.approx(0)


def test_zero_activity_day_renders_without_crashing(conn):
    report = builder.load_report(conn, "2026-09-01")

    assert report.category_totals == []
    assert report.total_tracked_minutes == 0.0
    assert report.top_windows == []
    assert report.meetings == []
    assert report.commits_by_repo == []
    assert report.wip_by_repo == []
    assert report.draft_lines == []
    assert report.status == "draft"


# --- DST correctness: real elapsed time, not naive wall-clock diff ---------


def test_activity_duration_is_correct_across_spring_forward_dst():
    """America/New_York, 2026-03-08: clocks jump from 01:59:59-05:00
    straight to 03:00:00-04:00. A block recorded from 01:30 to 03:30
    *looks* like 2 wall-clock hours but only 1 hour actually elapsed —
    tz-aware arithmetic (comparing real UTC instants) must get this
    right, not the naive wall-clock subtraction that would say 120."""
    from daylog.report.types import ActivityBlockInfo
    import datetime as dt

    start = dt.datetime.fromisoformat("2026-03-08T01:30:00-05:00")
    end = dt.datetime.fromisoformat("2026-03-08T03:30:00-04:00")
    block = ActivityBlockInfo(app="Code", title="x", category="Coding", start=start, end=end)

    assert block.minutes == pytest.approx(60.0)


def test_category_totals_correct_across_dst_boundary(conn):
    day = "2026-03-08"
    # Same real scenario, going through storage + load_report end to end.
    storage.insert_activity_block(conn, day, "2026-03-08T01:30:00-05:00", "2026-03-08T03:30:00-04:00", "Code", "x", "Coding")

    report = builder.load_report(conn, day)

    assert dict(report.category_totals)["Coding"] == pytest.approx(60.0)
    assert report.total_tracked_minutes == pytest.approx(60.0)


# --- commits / wip / meetings grouping ----------------------------------


def test_commits_are_grouped_by_repo_with_correct_sums(conn):
    day = "2026-09-01"
    storage.replace_commits_cache(conn, day, [
        {"repo": "proj-a", "hash": "aaa1", "subject": "fix bug", "branch": "main", "timestamp": "2026-09-01T09:00:00+00:00", "additions": 5, "deletions": 1},
        {"repo": "proj-a", "hash": "aaa2", "subject": "add feature", "branch": "main", "timestamp": "2026-09-01T10:00:00+00:00", "additions": 20, "deletions": 3},
        {"repo": "proj-b", "hash": "bbb1", "subject": "refactor module", "branch": "dev", "timestamp": "2026-09-01T11:00:00+00:00", "additions": 8, "deletions": 8},
    ])

    report = builder.load_report(conn, day)

    by_repo = {rc.repo: rc for rc in report.commits_by_repo}
    assert by_repo["proj-a"].additions == 25
    assert by_repo["proj-a"].deletions == 4
    assert len(by_repo["proj-a"].commits) == 2
    assert by_repo["proj-b"].additions == 8
    assert by_repo["proj-b"].deletions == 8


def test_wip_is_grouped_by_repo(conn):
    day = "2026-09-01"
    storage.replace_wip_cache(conn, day, [
        {"repo": "proj-a", "files": [{"path": "a.py", "status": "M"}, {"path": "b.py", "status": "??"}]},
    ])
    report = builder.load_report(conn, day)
    assert report.wip_by_repo[0].repo == "proj-a"
    assert len(report.wip_by_repo[0].files) == 2


def test_meetings_carry_all_day_flag_and_exact_duration(conn):
    day = "2026-09-01"
    storage.replace_meetings_cache(conn, day, [
        {"uid": "m1", "title": "Standup", "start": "2026-09-01T09:00:00+00:00", "end": "2026-09-01T09:15:00+00:00", "calendar_source": "work", "all_day": False},
        {"uid": "m2", "title": "Offsite", "start": "2026-09-01T00:00:00+00:00", "end": "2026-09-02T00:00:00+00:00", "calendar_source": "work", "all_day": True},
    ])
    report = builder.load_report(conn, day)

    by_title = {m.title: m for m in report.meetings}
    assert by_title["Standup"].minutes == pytest.approx(15.0)
    assert by_title["Standup"].all_day is False
    assert by_title["Offsite"].all_day is True
    assert by_title["Offsite"].minutes == pytest.approx(24 * 60)


# --- status reflects day_summaries --------------------------------------


def test_report_status_reflects_day_summary(conn):
    day = "2026-09-01"
    assert builder.load_report(conn, day).status == "draft"

    storage.save_generated_summary(conn, day, "- did work")
    storage.save_edited_summary(conn, day, "- did work (edited)")
    assert builder.load_report(conn, day).status == "ready"

    storage.submit_day(conn, day)
    assert builder.load_report(conn, day).status == "submitted"


# --- refresh_day / generate_report ---------------------------------------


def test_refresh_day_with_no_repos_or_calendars_configured(conn):
    cfg = default_config()
    cfg.git.scan_paths = []
    cfg.calendar.ics_urls = []

    result = builder.refresh_day(cfg, conn, "2026-09-01")

    assert result.git_available is True  # git binary presumably installed in test env
    assert result.calendar_available is True  # no sources configured = not a failure
    assert storage.get_commits(conn, "2026-09-01") == []
    assert storage.get_meetings(conn, "2026-09-01") == []


def test_refresh_day_preserves_cache_when_git_becomes_unavailable(conn, monkeypatch):
    day = "2026-09-01"
    storage.replace_commits_cache(conn, day, [
        {"repo": "old-repo", "hash": "zzz1", "subject": "old work", "branch": "main", "timestamp": "2026-09-01T09:00:00+00:00", "additions": 1, "deletions": 0},
    ])

    from daylog.collectors.git import GitCollection

    def fake_collect(config, day):
        return GitCollection(available=False, error="git is not installed or not on PATH")

    monkeypatch.setattr(builder.git_collector, "collect", fake_collect)

    cfg = default_config()
    result = builder.refresh_day(cfg, conn, day)

    assert result.git_available is False
    assert result.git_error is not None
    # Stale cache must survive — this is the "old report still renders"
    # guarantee even mid-refresh, not just for a plain read.
    commits = storage.get_commits(conn, day)
    assert len(commits) == 1
    assert commits[0]["repo"] == "old-repo"


def test_generate_report_overlays_fresh_availability(conn, monkeypatch):
    from daylog.collectors.calendar import CalendarCollection

    def fake_git_collect(config, day):
        from daylog.collectors.git import GitCollection
        return GitCollection(available=False, error="git is not installed or not on PATH")

    def fake_cal_collect(config, day):
        return CalendarCollection(sources=[])

    monkeypatch.setattr(builder.git_collector, "collect", fake_git_collect)
    monkeypatch.setattr(builder.calendar_collector, "collect", fake_cal_collect)

    cfg = default_config()
    report = builder.generate_report(cfg, conn, "2026-09-01")

    assert report.git_available is False
    assert "git is not installed" in report.git_error
    assert report.calendar_available is True


# --- optional LLM polish (Phase 9) -----------------------------------------


def test_load_report_never_uses_llm_even_when_enabled(conn):
    """load_report() has no config parameter at all — it's structurally
    incapable of calling the LLM, which is the point (pure read, see the
    module docstring)."""
    day = "2026-09-01"
    storage.replace_commits_cache(conn, day, [
        {"repo": "proj", "hash": "aaa1", "subject": "add retry logic", "branch": "main",
         "timestamp": f"{day}T09:00:00+00:00", "additions": 5, "deletions": 1},
    ])
    report = builder.load_report(conn, day)
    assert report.llm_used is False
    assert report.draft_text == "- Added retry logic in proj."


def _mock_one_commit_collector(monkeypatch):
    """generate_report() always calls refresh_day() first, which re-runs
    the real git collector and would wipe a hand-seeded commits_cache row
    (git is "available", just finds nothing at the default — nonexistent
    — scan_paths). Mocking the collector is what makes a seeded commit
    survive through to generate_report()'s draft."""
    from daylog.collectors.git import Commit, GitCollection, RepoResult

    commit = Commit(
        repo="proj", hash="aaa1234567", subject="add retry logic", branch="main",
        timestamp="2026-09-01T09:00:00+00:00", additions=5, deletions=1,
    )
    result = GitCollection(available=True, repos=[RepoResult(name="proj", path="/tmp/proj", commits=[commit])])
    monkeypatch.setattr(builder.git_collector, "collect", lambda config, day: result)


def test_generate_report_uses_plain_draft_when_llm_disabled(conn, monkeypatch):
    day = "2026-09-01"
    _mock_one_commit_collector(monkeypatch)
    cfg = default_config()
    assert cfg.llm.enabled is False

    report = builder.generate_report(cfg, conn, day)
    assert report.llm_used is False
    assert report.llm_error is None
    assert report.draft_text == "- Added retry logic in proj."


def test_generate_report_uses_polished_text_when_llm_succeeds(conn, monkeypatch):
    day = "2026-09-01"
    _mock_one_commit_collector(monkeypatch)
    cfg = default_config()
    cfg.llm.enabled = True

    import daylog.llm as llm_module
    monkeypatch.setattr(llm_module, "polish_draft", lambda lines, model=None: ("- Improved upload reliability.", None))

    report = builder.generate_report(cfg, conn, day)
    assert report.llm_used is True
    assert report.llm_error is None
    assert report.draft_text == "- Improved upload reliability."


def test_generate_report_falls_back_to_plain_draft_when_llm_fails(conn, monkeypatch):
    day = "2026-09-01"
    _mock_one_commit_collector(monkeypatch)
    cfg = default_config()
    cfg.llm.enabled = True

    import daylog.llm as llm_module
    monkeypatch.setattr(llm_module, "polish_draft", lambda lines, model=None: (None, "ANTHROPIC_API_KEY is not set"))

    report = builder.generate_report(cfg, conn, day)
    assert report.llm_used is False
    assert report.llm_error == "ANTHROPIC_API_KEY is not set"
    assert report.draft_text == "- Added retry logic in proj."  # fell back, not lost


def test_generate_report_skips_llm_when_there_is_no_draft(conn, monkeypatch):
    day = "2026-09-01"  # no commits/meetings seeded — nothing to polish
    cfg = default_config()
    cfg.llm.enabled = True

    import daylog.llm as llm_module
    calls = {"n": 0}
    monkeypatch.setattr(llm_module, "polish_draft", lambda lines, model=None: calls.__setitem__("n", calls["n"] + 1) or (None, None))

    report = builder.generate_report(cfg, conn, day)
    assert calls["n"] == 0  # never called — nothing to polish
    assert report.draft_text == ""
    assert report.llm_used is False


# --- week_overview (Phase 9 read-only week dashboard) -----------------------


def test_week_overview_aggregates_seven_days(conn):
    end_day = "2026-09-07"
    for offset, minutes in [(0, 30), (3, 60), (6, 90)]:
        day = (dt.date.fromisoformat(end_day) - dt.timedelta(days=6 - offset)).isoformat()
        end = (dt.datetime.fromisoformat(f"{day}T09:00:00+00:00") + dt.timedelta(minutes=minutes)).isoformat()
        storage.insert_activity_block(conn, day, f"{day}T09:00:00+00:00", end, "Code", "x", "Coding")

    result = builder.week_overview(conn, end_day)

    assert result["start"] == "2026-09-01"
    assert result["end"] == "2026-09-07"
    assert len(result["days"]) == 7
    assert result["total_tracked_minutes"] == pytest.approx(30 + 60 + 90)
    assert dict(zip([d["day"] for d in result["days"]], [d["total_tracked_minutes"] for d in result["days"]]))["2026-09-01"] == 30.0


def test_week_overview_marks_days_with_no_summary_as_missed(conn):
    day = "2026-09-01"
    storage.save_generated_summary(conn, day, "- did work")

    result = builder.week_overview(conn, "2026-09-07")
    by_day = {d["day"]: d["status"] for d in result["days"]}
    assert by_day[day] == "draft"
    assert by_day["2026-09-02"] is None  # never generated -> "Missed" in the UI


def test_week_overview_aggregates_categories_across_days(conn):
    storage.insert_activity_block(conn, "2026-09-01", "2026-09-01T09:00:00+00:00", "2026-09-01T09:30:00+00:00", "Code", "x", "Coding")
    storage.insert_activity_block(conn, "2026-09-02", "2026-09-02T09:00:00+00:00", "2026-09-02T09:15:00+00:00", "Code", "x", "Coding")
    storage.insert_activity_block(conn, "2026-09-03", "2026-09-03T09:00:00+00:00", "2026-09-03T09:20:00+00:00", "Teams", "x", "Meetings")

    result = builder.week_overview(conn, "2026-09-07")
    by_cat = {c["category"]: c["minutes"] for c in result["category_totals"]}
    assert by_cat["Coding"] == pytest.approx(45.0)
    assert by_cat["Meetings"] == pytest.approx(20.0)


def test_week_overview_empty_week_does_not_crash(conn):
    result = builder.week_overview(conn, "2026-09-07")
    assert result["total_tracked_minutes"] == 0.0
    assert result["total_commits"] == 0
    assert result["category_totals"] == []
    assert len(result["days"]) == 7
