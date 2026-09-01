"""Filesystem locations used by daylog.

Every other module reaches config.json and daylog.db through these
functions rather than hardcoding paths, so tests can redirect everything
into a temp directory by setting the DAYLOG_HOME environment variable
instead of touching the real ~/.daylog.
"""
from __future__ import annotations

import os
from pathlib import Path


def data_dir() -> Path:
    override = os.environ.get("DAYLOG_HOME")
    if override:
        return Path(override)
    return Path.home() / ".daylog"


def config_path() -> Path:
    return data_dir() / "config.json"


def db_path() -> Path:
    return data_dir() / "daylog.db"
