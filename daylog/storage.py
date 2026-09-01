"""SQLite schema, migrations, and all queries daylog runs.

Every other module talks to the database through the functions in this
file — nothing outside storage.py writes SQL. That keeps the schema (and
the invariants around it, like "never silently overwrite a submitted day")
enforceable in one place.

Migrations are plain idempotent SQL scripts (CREATE TABLE IF NOT EXISTS /
CREATE INDEX IF NOT EXISTS) tracked by a single-row schema_version table.
Adding a migration means appending a new (version, sql) tuple to
SCHEMA_MIGRATIONS — never editing an already-shipped one.
"""
from __future__ import annotations

import datetime as _dt
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Iterator, NamedTuple, Optional

from .paths import db_path

_MIGRATION_1 = """
CREATE TABLE IF NOT EXISTS day_summaries (
  day            TEXT PRIMARY KEY,   -- YYYY-MM-DD
  generated_md   TEXT,               -- what the tool produced
  edited_md      TEXT,               -- what the user changed it to, NULL if untouched
  status         TEXT NOT NULL DEFAULT 'draft',  -- draft | ready | submitted
  submitted_at   TEXT,
  created_at     TEXT NOT NULL,
  updated_at     TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS activity_blocks (
  id       INTEGER PRIMARY KEY AUTOINCREMENT,
  day      TEXT NOT NULL,
  start    TEXT NOT NULL,   -- ISO 8601, local time with offset
  end      TEXT NOT NULL,
  app      TEXT NOT NULL,
  title    TEXT NOT NULL,
  category TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_activity_blocks_day ON activity_blocks(day);

-- Commits and meetings are cached per day (not re-derived live) so an old
-- report still renders after a repo is deleted or a meeting disappears
-- from the calendar.
CREATE TABLE IF NOT EXISTS commits_cache (
  day         TEXT NOT NULL,
  repo        TEXT NOT NULL,
  hash        TEXT NOT NULL,
  subject     TEXT NOT NULL,
  branch      TEXT,
  timestamp   TEXT NOT NULL,
  additions   INTEGER NOT NULL DEFAULT 0,
  deletions   INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (day, repo, hash)
);

CREATE TABLE IF NOT EXISTS wip_cache (
  day   TEXT NOT NULL,
  repo  TEXT NOT NULL,
  files TEXT NOT NULL,  -- JSON list of {path, status}
  PRIMARY KEY (day, repo)
);

CREATE TABLE IF NOT EXISTS meetings_cache (
  day             TEXT NOT NULL,
  uid             TEXT NOT NULL,
  title           TEXT NOT NULL,
  start           TEXT NOT NULL,
  end             TEXT NOT NULL,
  calendar_source TEXT,
  PRIMARY KEY (day, uid)
);
"""

SCHEMA_MIGRATIONS: list[tuple[int, str]] = [
    (1, _MIGRATION_1),
]


class StorageError(Exception):
    """Raised for storage-layer failures: invalid state transitions, bad input."""


class DaySummary(NamedTuple):
    day: str
    generated_md: Optional[str]
    edited_md: Optional[str]
    status: str
    submitted_at: Optional[str]
    created_at: str
    updated_at: str

    @property
    def current_text(self) -> Optional[str]:
        """The text that should be shown/pasted: the edit if there is one, else the generated draft."""
        return self.edited_md if self.edited_md is not None else self.generated_md


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).isoformat()


def connect(path: Optional[Path] = None) -> sqlite3.Connection:
    p = path or db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(p), timeout=30, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def migrate(conn: sqlite3.Connection) -> list[int]:
    conn.execute("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    current = row["version"] if row else 0
    has_row = row is not None
    applied: list[int] = []
    for version, sql in SCHEMA_MIGRATIONS:
        if version <= current:
            continue
        conn.executescript(sql)
        if has_row:
            conn.execute("UPDATE schema_version SET version = ?", (version,))
        else:
            conn.execute("INSERT INTO schema_version (version) VALUES (?)", (version,))
            has_row = True
        current = version
        applied.append(version)
    return applied


@contextmanager
def open_db(path: Optional[Path] = None) -> Iterator[sqlite3.Connection]:
    """Open a connection with migrations applied, closing it on exit."""
    conn = connect(path)
    try:
        migrate(conn)
        yield conn
    finally:
        conn.close()


# --- day_summaries ---------------------------------------------------------


def _row_to_summary(row: sqlite3.Row) -> DaySummary:
    return DaySummary(
        day=row["day"],
        generated_md=row["generated_md"],
        edited_md=row["edited_md"],
        status=row["status"],
        submitted_at=row["submitted_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def get_day_summary(conn: sqlite3.Connection, day: str) -> Optional[DaySummary]:
    row = conn.execute("SELECT * FROM day_summaries WHERE day = ?", (day,)).fetchone()
    return _row_to_summary(row) if row else None


def list_day_summaries(conn: sqlite3.Connection, limit: int = 30) -> list[DaySummary]:
    rows = conn.execute(
        "SELECT * FROM day_summaries ORDER BY day DESC LIMIT ?", (limit,)
    ).fetchall()
    return [_row_to_summary(r) for r in rows]


def has_unsaved_edits(conn: sqlite3.Connection, day: str) -> bool:
    """True if edited_md exists and differs from generated_md.

    Callers (CLI/API) should check this before regenerating and warn the
    user, since save_generated_summary() will not touch edited_md itself
    but the caller may be about to discard the fact that it now disagrees
    with the new generated text.
    """
    existing = get_day_summary(conn, day)
    if not existing or existing.edited_md is None:
        return False
    return existing.edited_md != existing.generated_md


def save_generated_summary(conn: sqlite3.Connection, day: str, generated_md: str) -> DaySummary:
    """Store a freshly generated report for `day`.

    Never touches edited_md. Refuses to run against a submitted day — call
    reopen_day() first.
    """
    existing = get_day_summary(conn, day)
    if existing and existing.status == "submitted":
        raise StorageError(f"{day} is already submitted. Call reopen_day() before regenerating.")
    now = _now()
    if existing:
        conn.execute(
            "UPDATE day_summaries SET generated_md = ?, updated_at = ? WHERE day = ?",
            (generated_md, now, day),
        )
    else:
        conn.execute(
            "INSERT INTO day_summaries "
            "(day, generated_md, edited_md, status, submitted_at, created_at, updated_at) "
            "VALUES (?, ?, NULL, 'draft', NULL, ?, ?)",
            (day, generated_md, now, now),
        )
    result = get_day_summary(conn, day)
    assert result is not None
    return result


def save_edited_summary(conn: sqlite3.Connection, day: str, edited_md: str) -> DaySummary:
    existing = get_day_summary(conn, day)
    if existing and existing.status == "submitted":
        raise StorageError(f"{day} is already submitted. Call reopen_day() before editing.")
    now = _now()
    if existing:
        status = "ready" if existing.status == "draft" else existing.status
        conn.execute(
            "UPDATE day_summaries SET edited_md = ?, status = ?, updated_at = ? WHERE day = ?",
            (edited_md, status, now, day),
        )
    else:
        conn.execute(
            "INSERT INTO day_summaries "
            "(day, generated_md, edited_md, status, submitted_at, created_at, updated_at) "
            "VALUES (?, NULL, ?, 'ready', NULL, ?, ?)",
            (day, edited_md, now, now),
        )
    result = get_day_summary(conn, day)
    assert result is not None
    return result


def submit_day(conn: sqlite3.Connection, day: str) -> DaySummary:
    existing = get_day_summary(conn, day)
    if not existing or existing.current_text is None:
        raise StorageError(f"{day} has no summary to submit yet.")
    if existing.status == "submitted":
        return existing
    now = _now()
    conn.execute(
        "UPDATE day_summaries SET status = 'submitted', submitted_at = ?, updated_at = ? WHERE day = ?",
        (now, now, day),
    )
    result = get_day_summary(conn, day)
    assert result is not None
    return result


def reopen_day(conn: sqlite3.Connection, day: str) -> DaySummary:
    existing = get_day_summary(conn, day)
    if not existing:
        raise StorageError(f"{day} has no summary to reopen.")
    if existing.status != "submitted":
        return existing
    now = _now()
    conn.execute(
        "UPDATE day_summaries SET status = 'ready', submitted_at = NULL, updated_at = ? WHERE day = ?",
        (now, day),
    )
    result = get_day_summary(conn, day)
    assert result is not None
    return result


# --- activity_blocks ---------------------------------------------------------


def insert_activity_block(
    conn: sqlite3.Connection, day: str, start: str, end: str, app: str, title: str, category: str
) -> int:
    cur = conn.execute(
        "INSERT INTO activity_blocks (day, start, end, app, title, category) VALUES (?, ?, ?, ?, ?, ?)",
        (day, start, end, app, title, category),
    )
    return cur.lastrowid


def get_activity_blocks(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM activity_blocks WHERE day = ? ORDER BY start", (day,)
    ).fetchall()


# --- commits_cache / wip_cache / meetings_cache ------------------------------
# These are wholesale-replaced on each collection run: the collector always
# has the full current picture for `day`, so we drop old rows and reinsert
# rather than trying to diff.


def replace_commits_cache(conn: sqlite3.Connection, day: str, commits: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM commits_cache WHERE day = ?", (day,))
    conn.executemany(
        "INSERT INTO commits_cache (day, repo, hash, subject, branch, timestamp, additions, deletions) "
        "VALUES (:day, :repo, :hash, :subject, :branch, :timestamp, :additions, :deletions)",
        [{**c, "day": day} for c in commits],
    )


def get_commits(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM commits_cache WHERE day = ? ORDER BY repo, timestamp", (day,)
    ).fetchall()


def replace_wip_cache(conn: sqlite3.Connection, day: str, wip: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM wip_cache WHERE day = ?", (day,))
    conn.executemany(
        "INSERT INTO wip_cache (day, repo, files) VALUES (:day, :repo, :files)",
        [{"day": day, "repo": w["repo"], "files": json.dumps(w["files"])} for w in wip],
    )


def get_wip(conn: sqlite3.Connection, day: str) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT repo, files FROM wip_cache WHERE day = ?", (day,)).fetchall()
    return [{"repo": r["repo"], "files": json.loads(r["files"])} for r in rows]


def replace_meetings_cache(conn: sqlite3.Connection, day: str, meetings: list[dict[str, Any]]) -> None:
    conn.execute("DELETE FROM meetings_cache WHERE day = ?", (day,))
    conn.executemany(
        "INSERT INTO meetings_cache (day, uid, title, start, end, calendar_source) "
        "VALUES (:day, :uid, :title, :start, :end, :calendar_source)",
        [{**m, "day": day} for m in meetings],
    )


def get_meetings(conn: sqlite3.Connection, day: str) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT * FROM meetings_cache WHERE day = ? ORDER BY start", (day,)
    ).fetchall()
