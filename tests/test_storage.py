import pytest

from daylog import storage
from daylog.storage import StorageError


@pytest.fixture
def conn(daylog_home):
    with storage.open_db() as c:
        yield c


def test_migrate_creates_all_tables(conn):
    tables = {
        row["name"]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    }
    assert {
        "schema_version",
        "day_summaries",
        "activity_blocks",
        "commits_cache",
        "wip_cache",
        "meetings_cache",
    } <= tables


def test_migrate_is_idempotent(conn):
    # open_db() already migrated once via the fixture; running again should
    # be a no-op, not an error.
    applied_again = storage.migrate(conn)
    assert applied_again == []


def test_day_summary_lifecycle_preserves_edits_across_regenerate(conn):
    day = "2026-09-01"

    storage.save_generated_summary(conn, day, "- Generated A")
    assert storage.has_unsaved_edits(conn, day) is False

    storage.save_edited_summary(conn, day, "- Edited by hand")
    assert storage.has_unsaved_edits(conn, day) is True

    summary = storage.save_generated_summary(conn, day, "- Generated B")
    # Regenerating must not touch the hand-edited text.
    assert summary.edited_md == "- Edited by hand"
    assert summary.generated_md == "- Generated B"
    assert summary.current_text == "- Edited by hand"


def test_submit_then_regenerate_is_blocked_until_reopened(conn):
    day = "2026-09-01"
    storage.save_generated_summary(conn, day, "- Work done")
    storage.submit_day(conn, day)

    with pytest.raises(StorageError):
        storage.save_generated_summary(conn, day, "- New text")

    with pytest.raises(StorageError):
        storage.save_edited_summary(conn, day, "- New text")

    reopened = storage.reopen_day(conn, day)
    assert reopened.status == "ready"
    assert reopened.submitted_at is None

    updated = storage.save_generated_summary(conn, day, "- New text")
    assert updated.generated_md == "- New text"


def test_submit_requires_existing_content(conn):
    with pytest.raises(StorageError):
        storage.submit_day(conn, "2026-09-01")


def test_reopen_nonexistent_day_raises(conn):
    with pytest.raises(StorageError):
        storage.reopen_day(conn, "2026-09-01")


def test_reopen_non_submitted_day_is_a_noop(conn):
    day = "2026-09-01"
    storage.save_generated_summary(conn, day, "- Work done")
    result = storage.reopen_day(conn, day)
    assert result.status == "draft"


def test_list_day_summaries_orders_newest_first(conn):
    for day in ["2026-08-30", "2026-09-01", "2026-08-31"]:
        storage.save_generated_summary(conn, day, f"- work on {day}")

    days = [s.day for s in storage.list_day_summaries(conn)]
    assert days == ["2026-09-01", "2026-08-31", "2026-08-30"]


def test_activity_blocks_insert_and_query(conn):
    day = "2026-09-01"
    storage.insert_activity_block(conn, day, "09:00", "09:30", "Code", "editor - project", "Coding")
    storage.insert_activity_block(conn, day, "09:30", "10:00", "Teams", "Standup", "Meetings")
    storage.insert_activity_block(conn, day, "08:00", "08:30", "Code", "editor", "Coding")

    blocks = storage.get_activity_blocks(conn, day)
    assert [b["start"] for b in blocks] == ["08:00", "09:00", "09:30"]
    assert storage.get_activity_blocks(conn, "2026-01-01") == []


def test_commits_cache_replace_overwrites_previous_run(conn):
    day = "2026-09-01"
    storage.replace_commits_cache(
        conn,
        day,
        [
            {
                "repo": "work-log",
                "hash": "aaa111",
                "subject": "fix null ptr in parser",
                "branch": "main",
                "timestamp": "2026-09-01T09:00:00",
                "additions": 5,
                "deletions": 1,
            }
        ],
    )
    assert len(storage.get_commits(conn, day)) == 1

    storage.replace_commits_cache(
        conn,
        day,
        [
            {
                "repo": "work-log",
                "hash": "bbb222",
                "subject": "add report renderer",
                "branch": "main",
                "timestamp": "2026-09-01T10:00:00",
                "additions": 40,
                "deletions": 0,
            }
        ],
    )
    commits = storage.get_commits(conn, day)
    assert len(commits) == 1
    assert commits[0]["hash"] == "bbb222"


def test_wip_cache_round_trip(conn):
    day = "2026-09-01"
    storage.replace_wip_cache(
        conn, day, [{"repo": "work-log", "files": [{"path": "cli.py", "status": "M"}]}]
    )
    wip = storage.get_wip(conn, day)
    assert wip == [{"repo": "work-log", "files": [{"path": "cli.py", "status": "M"}]}]


def test_meetings_cache_round_trip(conn):
    day = "2026-09-01"
    storage.replace_meetings_cache(
        conn,
        day,
        [
            {
                "uid": "abc-123",
                "title": "Daily standup",
                "start": "2026-09-01T09:00:00",
                "end": "2026-09-01T09:15:00",
                "calendar_source": "work",
            }
        ],
    )
    meetings = storage.get_meetings(conn, day)
    assert len(meetings) == 1
    assert meetings[0]["title"] == "Daily standup"
