"""API endpoints tested with FastAPI's TestClient against a temporary
database (via the daylog_home fixture, same DAYLOG_HOME redirection every
other test module uses)."""
import pytest
from fastapi.testclient import TestClient

from daylog import storage
from daylog.config import default_config, save_config
from daylog.server.app import create_app


@pytest.fixture
def client(daylog_home):
    cfg = default_config()
    cfg.git.scan_paths = []  # keep tests hermetic — no real repo scanning
    cfg.calendar.ics_urls = []
    save_config(cfg)
    with storage.open_db() as conn:
        storage.migrate(conn)
    return TestClient(create_app())


def _seed_activity(day):
    with storage.open_db() as conn:
        storage.insert_activity_block(
            conn, day, f"{day}T09:00:00+00:00", f"{day}T10:00:00+00:00", "Code", "main.py", "Coding"
        )


# --- GET /api/status ---------------------------------------------------


def test_get_status_when_nothing_has_run(client):
    resp = client.get("/api/status")
    assert resp.status_code == 200
    data = resp.json()
    assert data["tracker_running"] is False
    assert data["last_sample_at"] is None


# --- GET/PUT /api/config -------------------------------------------------


def test_get_config_returns_current_config(client):
    resp = client.get("/api/config")
    assert resp.status_code == 200
    assert resp.json()["server"]["port"] == 8765


def test_put_config_updates_and_persists(client):
    resp = client.get("/api/config")
    body = resp.json()
    body["tracking"]["poll_interval_seconds"] = 10

    resp = client.put("/api/config", json=body)
    assert resp.status_code == 200
    assert resp.json()["tracking"]["poll_interval_seconds"] == 10

    # persisted, not just echoed back
    resp2 = client.get("/api/config")
    assert resp2.json()["tracking"]["poll_interval_seconds"] == 10


def test_put_config_rejects_unsafe_host(client):
    resp = client.get("/api/config")
    body = resp.json()
    body["server"]["host"] = "0.0.0.0"

    resp = client.put("/api/config", json=body)
    assert resp.status_code == 400
    assert "127.0.0.1" in resp.json()["detail"] or "network" in resp.json()["detail"]


# --- GET /api/days, GET /api/days/{day} -----------------------------------


def test_get_days_empty_when_nothing_tracked(client):
    resp = client.get("/api/days")
    assert resp.status_code == 200
    assert resp.json()["days"] == []


def test_get_days_lists_known_days_with_totals_and_status(client):
    _seed_activity("2026-09-01")
    with storage.open_db() as conn:
        storage.save_generated_summary(conn, "2026-09-01", "- did work")

    resp = client.get("/api/days")
    assert resp.status_code == 200
    days = resp.json()["days"]
    assert len(days) == 1
    assert days[0]["day"] == "2026-09-01"
    assert days[0]["status"] == "draft"
    assert days[0]["total_tracked_minutes"] == 60.0


def test_get_day_with_no_data_returns_empty_report_not_500(client):
    resp = client.get("/api/days/2026-01-01")
    assert resp.status_code == 200
    data = resp.json()
    assert data["day"] == "2026-01-01"
    assert data["category_totals"] == []
    assert data["draft_lines"] == []


def test_get_day_invalid_date_returns_400(client):
    resp = client.get("/api/days/not-a-date")
    assert resp.status_code == 400


def test_get_day_returns_full_report_shape(client):
    _seed_activity("2026-09-01")
    resp = client.get("/api/days/2026-09-01")
    data = resp.json()
    assert data["total_tracked_minutes"] == 60.0
    assert len(data["timeline"]) == 1
    assert data["timeline"][0]["category"] == "Coding"


def test_get_day_includes_persisted_summary_text(client):
    day = "2026-09-01"
    with storage.open_db() as conn:
        storage.save_generated_summary(conn, day, "- generated text")
        storage.save_edited_summary(conn, day, "- my edit")

    resp = client.get(f"/api/days/{day}")
    data = resp.json()
    assert data["generated_md"] == "- generated text"
    assert data["edited_md"] == "- my edit"
    assert data["current_text"] == "- my edit"


def test_get_day_summary_fields_are_null_with_no_summary_yet(client):
    resp = client.get("/api/days/2026-09-01")
    data = resp.json()
    assert data["generated_md"] is None
    assert data["edited_md"] is None
    assert data["current_text"] is None


# --- POST /api/days/{day}/regenerate --------------------------------------


def test_regenerate_runs_collectors_and_saves_generated_md(client):
    resp = client.post("/api/days/2026-09-01/regenerate")
    assert resp.status_code == 200
    data = resp.json()
    assert data["had_unsaved_edits"] is False
    assert data["generated_md"] is not None
    assert data["current_text"] == data["generated_md"]

    with storage.open_db() as conn:
        summary = storage.get_day_summary(conn, "2026-09-01")
    assert summary is not None
    assert summary.generated_md is not None


def test_regenerate_flags_had_unsaved_edits_without_destroying_them(client):
    day = "2026-09-01"
    with storage.open_db() as conn:
        storage.save_generated_summary(conn, day, "- original")
        storage.save_edited_summary(conn, day, "- my edit")

    resp = client.post(f"/api/days/{day}/regenerate")
    assert resp.status_code == 200
    assert resp.json()["had_unsaved_edits"] is True

    with storage.open_db() as conn:
        summary = storage.get_day_summary(conn, day)
    assert summary.edited_md == "- my edit"  # untouched


def test_regenerate_blocked_on_submitted_day(client):
    day = "2026-09-01"
    with storage.open_db() as conn:
        storage.save_generated_summary(conn, day, "- work")
        storage.submit_day(conn, day)

    resp = client.post(f"/api/days/{day}/regenerate")
    assert resp.status_code == 409

    with storage.open_db() as conn:
        summary = storage.get_day_summary(conn, day)
    assert summary.generated_md == "- work"  # untouched


def test_regenerate_invalid_date_returns_400(client):
    resp = client.post("/api/days/nope/regenerate")
    assert resp.status_code == 400


# --- PUT /api/days/{day}/summary -----------------------------------------


def test_put_summary_saves_edited_text(client):
    day = "2026-09-01"
    resp = client.put(f"/api/days/{day}/summary", json={"edited_md": "- my custom text"})
    assert resp.status_code == 200
    assert resp.json()["edited_md"] == "- my custom text"
    assert resp.json()["status"] == "ready"


def test_put_summary_blocked_on_submitted_day(client):
    day = "2026-09-01"
    with storage.open_db() as conn:
        storage.save_generated_summary(conn, day, "- work")
        storage.submit_day(conn, day)

    resp = client.put(f"/api/days/{day}/summary", json={"edited_md": "- try to edit"})
    assert resp.status_code == 409


def test_put_summary_missing_body_field_is_422(client):
    resp = client.put("/api/days/2026-09-01/summary", json={})
    assert resp.status_code == 422


# --- POST submit / reopen -------------------------------------------------


def test_submit_then_reopen_round_trip(client):
    day = "2026-09-01"
    with storage.open_db() as conn:
        storage.save_generated_summary(conn, day, "- work")

    resp = client.post(f"/api/days/{day}/submit")
    assert resp.status_code == 200
    assert resp.json()["status"] == "submitted"

    resp = client.post(f"/api/days/{day}/reopen")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ready"
    assert resp.json()["submitted_at"] is None


def test_submit_with_no_summary_yet_returns_409(client):
    resp = client.post("/api/days/2026-09-01/submit")
    assert resp.status_code == 409


def test_reopen_nonexistent_day_returns_409(client):
    resp = client.post("/api/days/2026-09-01/reopen")
    assert resp.status_code == 409


# --- GET /api/doctor -------------------------------------------------------


def test_get_doctor_returns_the_same_checks_as_the_cli(client):
    resp = client.get("/api/doctor")
    assert resp.status_code == 200
    data = resp.json()
    assert "checks" in data and "all_ok" in data
    labels = {c["label"] for c in data["checks"]}
    assert "Python >= 3.9" in labels
    assert "config.json valid" in labels


# --- POST /api/tracker/start, /api/tracker/stop ---------------------------


def test_tracker_start_and_stop_round_trip(client):
    resp = client.get("/api/status")
    assert resp.json()["tracker_running"] is False

    resp = client.post("/api/tracker/start")
    assert resp.status_code == 200
    pid = resp.json()["pid"]
    assert pid is not None

    import time

    for _ in range(100):
        if client.get("/api/status").json()["tracker_running"]:
            break
        time.sleep(0.05)
    assert client.get("/api/status").json()["tracker_running"] is True

    resp = client.post("/api/tracker/stop")
    assert resp.status_code == 200
    assert resp.json()["stopped"] is True
    assert client.get("/api/status").json()["tracker_running"] is False


def test_tracker_stop_when_not_running_is_a_noop(client):
    resp = client.post("/api/tracker/stop")
    assert resp.status_code == 200
    assert resp.json()["stopped"] is True


# --- GET /api/week (Phase 9 read-only week dashboard) -----------------------


def test_get_week_defaults_to_the_week_ending_today(client):
    resp = client.get("/api/week")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data["days"]) == 7
    import datetime

    assert data["end"] == datetime.date.today().isoformat()


def test_get_week_accepts_an_explicit_end_date(client):
    resp = client.get("/api/week", params={"date": "2026-09-07"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["start"] == "2026-09-01"
    assert data["end"] == "2026-09-07"


def test_get_week_aggregates_tracked_time(client):
    _seed_activity("2026-09-03")
    resp = client.get("/api/week", params={"date": "2026-09-07"})
    data = resp.json()
    assert data["total_tracked_minutes"] == 60.0
    by_day = {d["day"]: d for d in data["days"]}
    assert by_day["2026-09-03"]["total_tracked_minutes"] == 60.0
    assert by_day["2026-09-01"]["status"] is None  # never generated -> "Missed"


def test_get_week_invalid_date_returns_400(client):
    resp = client.get("/api/week", params={"date": "not-a-date"})
    assert resp.status_code == 400


def test_get_week_never_touches_collectors(client, monkeypatch):
    def boom(config, day):
        raise AssertionError("week dashboard must not call the git collector")

    monkeypatch.setattr("daylog.report.builder.git_collector.collect", boom)
    resp = client.get("/api/week", params={"date": "2026-09-07"})
    assert resp.status_code == 200


# --- LLM polish surfaced through the API (Phase 9) --------------------------


def test_regenerate_includes_llm_fields_when_disabled(client):
    resp = client.post("/api/days/2026-09-01/regenerate")
    data = resp.json()
    assert data["llm_used"] is False
    assert data["llm_error"] is None


def test_regenerate_uses_polished_text_when_llm_enabled(client, monkeypatch):
    import daylog.llm as llm_module
    from daylog.collectors.git import Commit, GitCollection, RepoResult

    commit = Commit(repo="proj", hash="aaa1234567", subject="add retry logic", branch="main",
                     timestamp="2026-09-01T09:00:00+00:00", additions=5, deletions=1)
    git_result = GitCollection(available=True, repos=[RepoResult(name="proj", path="/tmp/proj", commits=[commit])])
    monkeypatch.setattr("daylog.report.builder.git_collector.collect", lambda config, day: git_result)
    monkeypatch.setattr(llm_module, "polish_draft", lambda lines, model=None: ("- Improved reliability.", None))

    cfg = default_config()
    cfg.llm.enabled = True
    save_config(cfg)

    resp = client.post("/api/days/2026-09-01/regenerate")
    data = resp.json()
    assert data["llm_used"] is True
    assert data["generated_md"] == "- Improved reliability."
