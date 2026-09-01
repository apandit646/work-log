import json

from daylog import storage
from daylog.cli import main
from daylog.config import default_config, save_config


def _init_with_no_collectors(daylog_home):
    """Avoids the CLI report command actually hitting real git repos or
    calendar URLs during tests — scan_paths/ics_urls empty means the
    collectors run (cheaply) and find nothing, no mocking needed."""
    cfg = default_config()
    cfg.git.scan_paths = []
    cfg.calendar.ics_urls = []
    save_config(cfg)
    with storage.open_db() as conn:
        storage.migrate(conn)


def _seed_activity(day):
    with storage.open_db() as conn:
        storage.insert_activity_block(
            conn, day, f"{day}T09:00:00+00:00", f"{day}T10:00:00+00:00", "Code", "main.py", "Coding"
        )


def _mock_one_commit(monkeypatch, day):
    """cmd_report always calls refresh_day(), which re-runs the real git
    collector and would wipe any commits_cache row we pre-seeded by hand
    (git is "available", just finds nothing at the configured — empty —
    scan_paths). Mocking the collector is what makes a seeded commit
    actually survive through to the persisted draft."""
    from daylog.collectors.git import Commit, GitCollection, RepoResult

    commit = Commit(
        repo="proj", hash="abc1234567", subject="add retry logic", branch="main",
        timestamp=f"{day}T09:30:00+00:00", additions=5, deletions=1,
    )
    result = GitCollection(available=True, repos=[RepoResult(name="proj", path="/tmp/proj", commits=[commit])])
    monkeypatch.setattr("daylog.report.builder.git_collector.collect", lambda config, day: result)


def test_report_prints_the_full_markdown_report(daylog_home, capsys):
    _init_with_no_collectors(daylog_home)
    day = "2026-09-01"
    _seed_activity(day)

    exit_code = main(["report", "--date", day])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert f"# Daylog — {day}" in out
    assert "Coding" in out


def test_report_persists_only_the_draft_bullets_not_the_full_report(daylog_home, capsys, monkeypatch):
    # generated_md is what the summary textarea in the web UI holds and
    # what gets pasted into the office form — it must be just the plain-
    # language draft bullets, not the whole multi-section Markdown report
    # (headers, bar chart, commit lists) that gets printed/--out instead.
    _init_with_no_collectors(daylog_home)
    day = "2026-09-01"
    _seed_activity(day)
    _mock_one_commit(monkeypatch, day)

    exit_code = main(["report", "--date", day])
    assert exit_code == 0
    capsys.readouterr()

    with storage.open_db() as conn:
        summary = storage.get_day_summary(conn, day)
    assert summary is not None
    assert summary.generated_md == "- Added retry logic in proj."
    assert "# Daylog" not in summary.generated_md
    assert summary.edited_md is None


def test_report_json_flag_prints_valid_json(daylog_home, capsys):
    _init_with_no_collectors(daylog_home)
    day = "2026-09-01"
    _seed_activity(day)

    exit_code = main(["report", "--date", day, "--json"])
    assert exit_code == 0

    out = capsys.readouterr().out
    data = json.loads(out)
    assert data["day"] == day
    assert data["category_totals"][0]["category"] == "Coding"


def test_report_out_flag_writes_file(daylog_home, tmp_path):
    _init_with_no_collectors(daylog_home)
    day = "2026-09-01"
    _seed_activity(day)

    out_file = tmp_path / "report.md"
    exit_code = main(["report", "--date", day, "--out", str(out_file)])

    assert exit_code == 0
    assert out_file.exists()
    assert f"# Daylog — {day}" in out_file.read_text(encoding="utf-8")


def test_report_copy_flag_copies_draft_bullets(daylog_home, capsys, monkeypatch):
    _init_with_no_collectors(daylog_home)
    day = "2026-09-01"

    captured = {}

    def fake_copy(text):
        captured["text"] = text
        return True, None

    monkeypatch.setattr("daylog.cli.clipboard.copy_to_clipboard", fake_copy)

    exit_code = main(["report", "--date", day, "--copy"])

    assert exit_code == 0
    assert "text" in captured  # copy_to_clipboard was called
    out = capsys.readouterr().out
    assert "copied to clipboard" in out


def test_report_copy_flag_reports_failure_without_crashing(daylog_home, capsys, monkeypatch):
    _init_with_no_collectors(daylog_home)
    monkeypatch.setattr(
        "daylog.cli.clipboard.copy_to_clipboard", lambda text: (False, "no clipboard tool found")
    )

    exit_code = main(["report", "--date", "2026-09-01", "--copy"])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "Could not copy to clipboard: no clipboard tool found" in out


def test_report_invalid_date_is_rejected(daylog_home, capsys):
    _init_with_no_collectors(daylog_home)
    exit_code = main(["report", "--date", "not-a-date"])
    assert exit_code == 1
    assert "Invalid --date" in capsys.readouterr().out


def test_report_blocks_regeneration_of_a_submitted_day(daylog_home, capsys):
    _init_with_no_collectors(daylog_home)
    day = "2026-09-01"
    with storage.open_db() as conn:
        storage.save_generated_summary(conn, day, "- work done")
        storage.submit_day(conn, day)

    exit_code = main(["report", "--date", day])

    assert exit_code == 1
    out = capsys.readouterr().out
    assert "already submitted" in out

    with storage.open_db() as conn:
        summary = storage.get_day_summary(conn, day)
    assert summary.generated_md == "- work done"  # untouched


def test_report_warns_but_does_not_destroy_edits(daylog_home, capsys):
    _init_with_no_collectors(daylog_home)
    day = "2026-09-01"
    with storage.open_db() as conn:
        storage.save_generated_summary(conn, day, "- original draft")
        storage.save_edited_summary(conn, day, "- my hand-edited version")

    exit_code = main(["report", "--date", day])

    assert exit_code == 0
    out = capsys.readouterr().out
    assert "hand-edited text" in out

    with storage.open_db() as conn:
        summary = storage.get_day_summary(conn, day)
    assert summary.edited_md == "- my hand-edited version"  # preserved
    assert summary.generated_md != "- original draft"  # refreshed


def test_report_defaults_to_today(daylog_home, capsys):
    import datetime

    _init_with_no_collectors(daylog_home)
    exit_code = main(["report"])
    assert exit_code == 0
    today = datetime.date.today().isoformat()
    assert f"# Daylog — {today}" in capsys.readouterr().out


def test_report_without_config_gives_a_clear_error(daylog_home, capsys):
    exit_code = main(["report"])
    assert exit_code == 1
    assert "daylog init" in capsys.readouterr().out
