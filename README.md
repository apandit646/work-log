# daylog

A local-first CLI that tracks what you actually did during the day —
active window time, git commits, calendar meetings — and drafts a
Markdown summary you can paste into an end-of-day timesheet form.

Everything lives on your machine at `~/.daylog/`. Nothing is uploaded
anywhere unless you explicitly opt into the optional LLM-polishing
feature (not built yet).

This is a phased build. **Phases 1–6 (skeleton/storage, window tracking,
git collector, calendar collector, report generation, JSON API) are
done.** The web UI and packaging/autostart land in later phases.

## Status: Phase 6

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
- `daylog.collectors.git` — discovers repos under `git.scan_paths`,
  collects today's commits (across all local branches, deduplicated,
  filtered to your identity) with lines added/removed, and lists
  uncommitted work in progress.
- `daylog report [--date YYYY-MM-DD] [--copy] [--out FILE] [--json]` —
  generates the full Markdown report (time by category with a text bar
  chart, meetings, commits by repo, work in progress, top windows, and
  the "Draft for the timesheet" block), prints it, and saves it as that
  day's `generated_md`. `--copy` copies just the timesheet draft bullets
  to the clipboard (via `clip` on Windows, `xclip`/`xsel` on Linux — no
  new dependency); `--out FILE` also writes the Markdown to a file;
  `--json` prints the same data as JSON instead. Refuses to touch an
  already-submitted day; warns (without blocking or discarding) if
  you've hand-edited that day's summary since it was last generated.

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
| `calendar.ics_urls` | `[]` | Private iCal URLs (Outlook/Microsoft 365 and Google Calendar both work — see "How the calendar collector works"). |
| `calendar.cache_minutes` | `15` | How long a fetched `.ics` is cached before re-downloading. |
| `calendar.owner_email` | `""` | Your email as it appears in `ATTENDEE` lines, used to find your own RSVP status and skip events you declined. Optional — a Google-style `STATUS:CANCELLED` decline is always honored even if this is blank. |
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

## How the git collector works

- Discovers repos by walking each `git.scan_paths` entry up to
  `git.scan_depth` levels, stopping (not descending further) as soon as it
  finds a `.git` — so a repo's own internals, and nested repos like
  submodules, are never scanned into. A path that doesn't exist or can't
  be read is skipped, not an error.
- "My commits" are decided by `user.git_author_patterns` — substrings
  matched case-insensitively against `"Name <email>"`. If that list is
  empty, each repo falls back to its own `git config user.name`/
  `user.email`; if neither is set, that repo simply contributes no
  commits (never a crash).
- Collects commits from every local branch, not just the current one, and
  deduplicates by hash so a commit reachable from two branches (e.g. a
  shared ancestor) is only counted once — attributed to whichever branch
  was processed first (current branch, then the rest). A detached HEAD is
  still walked and labeled `"(detached HEAD)"`.
- "Today" is decided by each commit's **author date** (not commit date,
  which can differ after a rebase), compared as a plain calendar-day
  string.
- Uncommitted work in progress comes from `git status --porcelain`:
  modified, added, deleted, renamed, and untracked files are all listed
  per repo.
- Nothing here raises for "no data" — no commits, an empty repo, a
  detached HEAD, git itself missing (`GitCollection.available = False`)
  all resolve to an empty/partial result. A single unreadable repo is
  caught individually (`RepoResult.error`) so it doesn't take down the
  whole collection.

`daylog report` runs this automatically now, but you can also call the
collector directly:

```bash
python3 - <<'EOF'
import datetime
from daylog.collectors import git
from daylog.config import default_config

cfg = default_config()
cfg.git.scan_paths = [r"C:\Users\me\source", "~/code"]  # edit to a real path to try it
cfg.user.git_author_patterns = ["you@example.com"]

result = git.collect(cfg, datetime.date.today().isoformat())
print("available:", result.available, result.error)
for repo in result.repos:
    print(repo.name, "-", len(repo.commits), "commits,", len(repo.wip), "changed files")
EOF
```

## How the calendar collector works

- Point `calendar.ics_urls` at your private iCal feed URL(s) — Outlook/
  Microsoft 365 (Settings → Calendar → Shared calendars → Publish a
  calendar → ICS link) and Google Calendar (Settings → *your calendar* →
  Integrate calendar → Secret address in iCal format) both expose the
  same kind of URL, so one code path covers both; no OAuth.
- Each feed is fetched over HTTPS and cached locally for
  `calendar.cache_minutes` (default 15) so re-running the report doesn't
  re-download every time. A network failure falls back to a stale cache
  if one exists, rather than losing the day's meetings over a flaky
  connection; with no cache at all, that source is marked unavailable
  with a reason instead of crashing the collection.
- Recurring events (`RRULE`) are expanded for the requested day only,
  honoring `EXDATE`. All-day events and per-event timezones (`VTIMEZONE`/
  `TZID`) are handled by the `icalendar`/`python-dateutil` parsing —
  see collectors/calendar.py's module docstring for the one documented
  scope gap (an individually-declined single occurrence of a recurring
  series, rather than the whole series or a Google-style cancelled
  export, may still appear once).
- Declined events are skipped: a Google-style `STATUS:CANCELLED` export
  is always honored; if `calendar.owner_email` is set, your own
  `ATTENDEE`/`PARTSTAT=DECLINED` is checked too.
## How report generation works

- `daylog report` always re-collects git and calendar data for the
  requested day and replaces that day's cached commits/meetings — this
  is "generate", and generating always reflects the *current* state of
  your repos and calendar, not a frozen snapshot. If a collector
  genuinely fails (git missing, calendar unreachable), the previous
  cache for that day is left untouched rather than wiped by an empty
  result — but if it *succeeds* and finds nothing (e.g. a repo was
  deleted from disk), the day's cache legitimately reflects that. A
  true point-in-time view that never re-collects — the mechanism that
  keeps an old day rendering correctly forever — is `daylog.report.
  builder.load_report()`, which the JSON API's `GET` endpoints will use
  starting in Phase 6; the CLI doesn't expose a separate "view without
  regenerating" command yet.
- **The timesheet draft** (`report/draft.py`) is a mechanical, rule-based
  rewrite, not an LLM: it strips conventional-commit prefixes
  (`fix: …`), maps a small set of known leading verbs to past tense
  (fix → Fixed, add → Added, …), and translates a short list of common
  technical jargon (null pointer → "a crash", race condition → "a timing
  bug", …). It cannot infer domain-specific meaning like turning "the
  parser" into "the invoice parser" — that needs real language
  understanding, which is exactly what the optional Phase 9 LLM-polish
  feature (off by default) is for. Multiple non-trivial commits in the
  same repo are merged into one line; `wip`/`typo`/formatting/merge
  commits are always excluded; meetings 15 minutes or under are excluded.
  Every line traces back to a real commit or meeting — nothing is
  invented.
- Categorization (`daylog.categorize`) is keyword-matched against app
  name + window title using `config.categories`, first match wins,
  computed once per activity block at tracking time (Phase 2) rather
  than at report time.

## The JSON API

Not started automatically yet — `daylog ui` (Phase 7) is what will run
it — but you can try it now:

```bash
python3 -c "import uvicorn; from daylog.server.app import app; uvicorn.run(app, host='127.0.0.1', port=8765)"
```

Bound to `127.0.0.1` only (`config.server.host` rejects anything else at
the config layer — see `config._validate`), no authentication, because
nothing but localhost can ever reach it. All aggregation happens in
`report.builder`/`report.render`; every route below just validates input
and calls those.

| Endpoint | Behavior |
|---|---|
| `GET /api/days?limit=30` | Per-day overview (status, total tracked minutes, commit count) for the most recent days with any footprint. `status` is `null` for a day that's been tracked but never reported. |
| `GET /api/days/{date}` | The full report as JSON — same shape as `daylog report --json`. Read-only: never touches git/calendar/network. A date with no data returns an empty report, not an error. |
| `POST /api/days/{date}/regenerate` | Re-runs the collectors and replaces that day's cache (same semantics as `daylog report`). Blocked (`409`) on a submitted day. Response includes `had_unsaved_edits: bool` — edited text is never destroyed, but this tells the caller it's now stale. |
| `PUT /api/days/{date}/summary` | Body `{"edited_md": "..."}`. Saves hand-edited text. Blocked (`409`) on a submitted day. |
| `POST /api/days/{date}/submit` | Marks the day submitted (`409` if there's no summary yet). |
| `POST /api/days/{date}/reopen` | Undoes submit. |
| `GET /api/status` | `{tracker_running, tracker_pid, last_sample_at}`. |
| `GET /api/config` / `PUT /api/config` | Read/replace the full config as JSON — same validation as `config.json` on disk (rejects a non-local `server.host`, etc). |

`GET /api/days/{date}`'s `timeline` array carries `{start, end, category,
app, title}` per block — `app`/`title` beyond the minimum so the web
UI's timeline hover (Phase 7) can show what was actually open, without a
second request.

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
