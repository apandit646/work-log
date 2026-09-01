# daylog

A local-first CLI that tracks what you actually did during the day —
active window time, git commits, calendar meetings — and drafts a
Markdown summary you can paste into an end-of-day timesheet form.

Everything lives on your machine at `~/.daylog/`. Nothing is uploaded
anywhere unless you explicitly opt into the optional LLM-polishing
feature (not built yet).

This is a phased build. **Phases 1–2 (skeleton/storage, window tracking)
are done.** Git/calendar collectors, report generation, the JSON API, and
the web UI land in later phases.

## Status: Phase 2

What exists right now:

- `daylog init` — writes a default `config.json` and creates/migrates the
  SQLite database.
- `daylog doctor` — checks Python version, git presence, platform-specific
  window-tracking prerequisites, config validity, and database
  writability; prints a pass/fail line for each.
- `daylog track` — runs in the foreground, polling the active window and
  idle time and writing merged time blocks to `activity_blocks`. Stop it
  with Ctrl+C. Refuses to start a second instance while one is already
  running (tracked via a pidfile at `~/.daylog/tracker.pid`).
- `daylog status` — reports whether the tracker is running and when it
  last recorded a sample.
- `daylog report` / `ui` exist as real subcommands (so `--help` shows the
  full planned shape of the tool) but currently just print which phase
  implements them.

## Install (development)

```bash
python -m venv .venv
source .venv/bin/activate        # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"
```

Requires Python 3.9+ and no admin/root privileges.

## Try it

```bash
daylog init
daylog doctor
daylog track      # runs until you press Ctrl+C
```

In another terminal, while `track` is running:

```bash
daylog status
```

`daylog init` is safe to re-run — it won't overwrite an existing
`config.json` unless you pass `--force`. It always re-runs database
migrations, which are idempotent.

## Run the tests

```bash
pytest
```

Tests never touch your real `~/.daylog` — they redirect it to a temp
directory via the `DAYLOG_HOME` environment variable (see
`tests/conftest.py`).

## Where data lives

- Config: `~/.daylog/config.json`
- Database: `~/.daylog/daylog.db` (SQLite, WAL mode)

Both paths can be redirected for testing/development by setting the
`DAYLOG_HOME` environment variable to point somewhere else.

## Configuration reference

`daylog init` writes a fully-populated `config.json`; every field below is
optional in the file on disk — anything you omit falls back to the
default shown. Delete a section entirely and it's regenerated from
defaults on next load.

| Key | Default | Meaning |
|---|---|---|
| `version` | `1` | Config schema version, bumped if the shape changes. |
| `user.git_author_patterns` | `[]` | Substrings/emails matched against commit author to decide which commits are "mine". Empty means the git collector falls back to each repo's `git config user.name`/`user.email`. |
| `tracking.poll_interval_seconds` | `5` | How often the window tracker samples the active window. |
| `tracking.idle_threshold_seconds` | `300` | Idle time (no keyboard/mouse input) after which a sample is dropped instead of recorded. |
| `git.scan_paths` | `["C:\\Users\\me\\source", "~/code"]` | Directories to walk looking for `.git` repos. Edit to match your machine — a path that doesn't exist is skipped, not an error. |
| `git.scan_depth` | `4` | How many directory levels deep to walk before giving up on a subtree. |
| `calendar.ics_urls` | `[]` | Private iCal URLs (Outlook or Google Calendar both work — see Phase 4). |
| `calendar.cache_minutes` | `15` | How long a fetched `.ics` is cached before re-downloading. |
| `categories` | Meetings / Coding / Browser / Communication / Other, each with keyword lists | Keyword-matched against app name and window title, first match wins, unmatched falls through to the last rule (`Other`). Must contain at least one rule. |
| `server.host` | `127.0.0.1` | Web UI bind address. Only `127.0.0.1` or `localhost` are accepted — daylog refuses to be reachable from the network. |
| `server.port` | `8765` | Web UI port. |

## `daylog doctor` checks

- Python version is 3.9 or newer.
- `git` is on `PATH`.
- **Windows**: `user32`/`kernel32` are reachable via `ctypes`.
- **Linux**: warns if the session is Wayland (window titles will be
  unavailable — switch to Xorg/X11, or accept idle-only tracking); checks
  for `xdotool` or `xprop` (window info) and `xprintidle` (optional; a
  ctypes/X11 fallback is used if absent).
- `config.json` parses and passes validation.
- The database file can be opened and queried.

## How window tracking works

- `daylog track` polls the active window and idle time every
  `tracking.poll_interval_seconds`. Consecutive polls of the same
  app+title on the same day extend one `activity_blocks` row's `end`
  column; a different window, an idle gap, or a day boundary starts a new
  row.
- **Kill-safe by construction**: the currently-open block's `end` is
  updated in the database on *every* matching poll, not just when the
  block finishes. So a sleep/reboot/crash loses at most one
  `poll_interval_seconds` of data, and restarting afterwards just begins
  a fresh block — nothing already written is touched, and nothing is
  double-counted.
- When idle time crosses `tracking.idle_threshold_seconds`, the current
  block is closed (at its last real update) and nothing is recorded
  until activity resumes; the idle gap itself is never written as a
  block.
- `daylog track` refuses to start a second instance (checked via
  `~/.daylog/tracker.pid`); `daylog status` reports whether it's running
  and reads the same pidfile, self-healing if the file is stale (process
  no longer exists).
- **Linux**: tries `xdotool` first, falls back to `xprop`; idle time
  tries `xprintidle`, falls back to the X11 screensaver extension via
  ctypes. Under Wayland, none of these see real window titles — `daylog
  doctor`/`track` print a warning up front rather than silently recording
  nothing.
- **Windows**: `ctypes` against `user32`/`kernel32`
  (`GetForegroundWindow`, `QueryFullProcessImageNameW`,
  `GetLastInputInfo`) — no `pywin32` dependency.

## Design notes for later phases

- `activity_blocks` stores merged time ranges (start/end per
  app+category), not one row per poll — see "How window tracking works"
  above.
- `commits_cache` and `meetings_cache` are snapshots per day, refreshed
  wholesale each time the collectors run, so an old report still renders
  correctly after a repo is deleted or a meeting is removed from the
  calendar.
- `day_summaries` keeps `generated_md` (what the tool produced) and
  `edited_md` (your hand edits) as separate columns. Regenerating never
  overwrites `edited_md`; callers should check `has_unsaved_edits()` first
  and warn before regenerating. A `submitted` day is frozen — both
  `save_generated_summary()` and `save_edited_summary()` raise until
  `reopen_day()` is called.
