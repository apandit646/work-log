"""JSON API endpoints. All aggregation happens in report.builder /
report.render — routes just validate input, call those, and translate
daylog's own exceptions (ConfigError, StorageError) into HTTP responses.

No authentication: this API is only ever reachable from 127.0.0.1 (see
config.py's validation of server.host, and daylog ui in Phase 8), so
there is nothing else that could be calling it.
"""
from __future__ import annotations

import datetime as _dt
from typing import Any, Dict

from fastapi import APIRouter, Body, HTTPException, Query
from pydantic import BaseModel

from .. import doctor, pidfile, storage, tracker_process
from ..config import ConfigError, config_from_dict, config_to_dict, load_config, save_config
from ..report import builder as report_builder
from ..report import render as report_render

router = APIRouter(prefix="/api")


def _validate_day(day: str) -> str:
    try:
        _dt.date.fromisoformat(day)
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Invalid date {day!r}: expected YYYY-MM-DD")
    return day


def _load_config_or_400():
    try:
        return load_config()
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))


class SummaryUpdate(BaseModel):
    edited_md: str


@router.get("/status")
def get_status() -> Dict[str, Any]:
    running, pid = pidfile.tracker_status()
    with storage.open_db() as conn:
        last_sample = storage.get_last_activity_end(conn)
    return {"tracker_running": running, "tracker_pid": pid, "last_sample_at": last_sample}


@router.get("/doctor")
def get_doctor() -> Dict[str, Any]:
    results = doctor.run_checks()
    return {"checks": results, "all_ok": doctor.all_required_ok(results)}


@router.post("/tracker/start")
def start_tracker() -> Dict[str, Any]:
    pid = tracker_process.start_tracker()
    return {"pid": pid}


@router.post("/tracker/stop")
def stop_tracker() -> Dict[str, Any]:
    stopped = tracker_process.stop_tracker()
    return {"stopped": stopped}


@router.get("/config")
def get_config() -> Dict[str, Any]:
    return config_to_dict(_load_config_or_400())


@router.put("/config")
def put_config(body: Dict[str, Any] = Body(...)) -> Dict[str, Any]:
    try:
        cfg = config_from_dict(body)
    except ConfigError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    save_config(cfg)
    return config_to_dict(cfg)


@router.get("/days")
def list_days(limit: int = Query(30, ge=1, le=365)) -> Dict[str, Any]:
    with storage.open_db() as conn:
        days = report_builder.list_days_overview(conn, limit)
    return {"days": days}


def _summary_fields(conn, day: str) -> Dict[str, Any]:
    """generated_md/edited_md/current_text/submitted_at/updated_at — the
    persisted, editable-by-hand fields that live in day_summaries, not on
    the freshly-aggregated Report. The web UI's summary textarea needs
    both in one response, so GET/regenerate merge this in."""
    summary = storage.get_day_summary(conn, day)
    if summary is None:
        return {
            "generated_md": None, "edited_md": None, "current_text": None,
            "submitted_at": None, "updated_at": None,
        }
    return {
        "generated_md": summary.generated_md,
        "edited_md": summary.edited_md,
        "current_text": summary.current_text,
        "submitted_at": summary.submitted_at,
        "updated_at": summary.updated_at,
    }


@router.get("/days/{day}")
def get_day(day: str) -> Dict[str, Any]:
    day = _validate_day(day)
    with storage.open_db() as conn:
        report = report_builder.load_report(conn, day)
        result = report_render.render_json(report)
        result.update(_summary_fields(conn, day))
    return result


@router.post("/days/{day}/regenerate")
def regenerate_day(day: str) -> Dict[str, Any]:
    day = _validate_day(day)
    cfg = _load_config_or_400()
    with storage.open_db() as conn:
        existing = storage.get_day_summary(conn, day)
        if existing and existing.status == "submitted":
            raise HTTPException(status_code=409, detail=f"{day} is already submitted; reopen it first")
        had_unsaved_edits = storage.has_unsaved_edits(conn, day)
        report = report_builder.generate_report(cfg, conn, day)
        # Only the timesheet-paste-ready bullets are persisted as
        # generated_md — see render_draft_text()'s docstring.
        storage.save_generated_summary(conn, day, report_render.render_draft_text(report))
        result = report_render.render_json(report)
        result.update(_summary_fields(conn, day))
    # Edits are never destroyed (generated_md and edited_md are separate
    # columns) — this just tells the client that stale edited text is now
    # sitting on top of freshly-regenerated data, so it can warn the user.
    result["had_unsaved_edits"] = had_unsaved_edits
    return result


@router.put("/days/{day}/summary")
def put_summary(day: str, body: SummaryUpdate) -> Dict[str, Any]:
    day = _validate_day(day)
    with storage.open_db() as conn:
        try:
            summary = storage.save_edited_summary(conn, day, body.edited_md)
        except storage.StorageError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return dict(summary._asdict())


@router.post("/days/{day}/submit")
def submit_day(day: str) -> Dict[str, Any]:
    day = _validate_day(day)
    with storage.open_db() as conn:
        try:
            summary = storage.submit_day(conn, day)
        except storage.StorageError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return dict(summary._asdict())


@router.post("/days/{day}/reopen")
def reopen_day(day: str) -> Dict[str, Any]:
    day = _validate_day(day)
    with storage.open_db() as conn:
        try:
            summary = storage.reopen_day(conn, day)
        except storage.StorageError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
    return dict(summary._asdict())
