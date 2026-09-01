"""Loading, validating, and saving config.json.

The file is optional per-key: any field missing from the JSON on disk is
filled in from default_config(), so config.json can grow across daylog
versions without breaking older files. Anything present but malformed
(wrong type, out-of-range value) raises ConfigError with a message that
says what to fix, rather than letting a KeyError/TypeError escape.
"""
from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any, Optional

from .paths import config_path

CONFIG_VERSION = 1


class ConfigError(Exception):
    """Raised when config.json is missing, malformed, or fails validation."""


@dataclasses.dataclass
class CategoryRule:
    name: str
    keywords: list[str]


@dataclasses.dataclass
class UserConfig:
    # Substrings/emails matched against git commit author to decide which
    # commits are "mine". Empty means the git collector falls back to
    # `git config user.name`/`user.email` per repo.
    git_author_patterns: list[str] = dataclasses.field(default_factory=list)


@dataclasses.dataclass
class TrackingConfig:
    poll_interval_seconds: int = 5
    idle_threshold_seconds: int = 300


@dataclasses.dataclass
class GitConfig:
    scan_paths: list[str] = dataclasses.field(default_factory=list)
    scan_depth: int = 4


@dataclasses.dataclass
class CalendarConfig:
    ics_urls: list[str] = dataclasses.field(default_factory=list)
    cache_minutes: int = 15
    # Your own email as it appears in ATTENDEE lines, used to find your
    # PARTSTAT and skip events you declined. Optional — STATUS:CANCELLED
    # (how Google's private feed marks a decline) is always honored even
    # if this is blank.
    owner_email: str = ""


@dataclasses.dataclass
class ServerConfig:
    host: str = "127.0.0.1"
    port: int = 8765


@dataclasses.dataclass
class Config:
    version: int
    user: UserConfig
    tracking: TrackingConfig
    git: GitConfig
    calendar: CalendarConfig
    categories: list[CategoryRule]
    server: ServerConfig


def default_config() -> Config:
    return Config(
        version=CONFIG_VERSION,
        user=UserConfig(git_author_patterns=[]),
        tracking=TrackingConfig(),
        git=GitConfig(
            scan_paths=[r"C:\Users\me\source", "~/code"],
            scan_depth=4,
        ),
        calendar=CalendarConfig(ics_urls=[], cache_minutes=15, owner_email=""),
        categories=[
            CategoryRule("Meetings", ["teams", "zoom", "webex", "meet"]),
            CategoryRule(
                "Coding",
                ["code", "pycharm", "intellij", "visual studio", "vim", "terminal", "console"],
            ),
            CategoryRule("Browser", ["chrome", "firefox", "edge", "safari"]),
            CategoryRule("Communication", ["outlook", "slack", "mail"]),
            CategoryRule("Other", []),
        ],
        server=ServerConfig(host="127.0.0.1", port=8765),
    )


def _cfg_to_dict(cfg: Config) -> dict[str, Any]:
    return {
        "version": cfg.version,
        "user": {"git_author_patterns": cfg.user.git_author_patterns},
        "tracking": {
            "poll_interval_seconds": cfg.tracking.poll_interval_seconds,
            "idle_threshold_seconds": cfg.tracking.idle_threshold_seconds,
        },
        "git": {"scan_paths": cfg.git.scan_paths, "scan_depth": cfg.git.scan_depth},
        "calendar": {
            "ics_urls": cfg.calendar.ics_urls,
            "cache_minutes": cfg.calendar.cache_minutes,
            "owner_email": cfg.calendar.owner_email,
        },
        "categories": [{"name": c.name, "keywords": c.keywords} for c in cfg.categories],
        "server": {"host": cfg.server.host, "port": cfg.server.port},
    }


def _dict_to_cfg(data: dict[str, Any]) -> Config:
    defaults = default_config()
    try:
        user_d = data.get("user", {}) or {}
        tracking_d = data.get("tracking", {}) or {}
        git_d = data.get("git", {}) or {}
        cal_d = data.get("calendar", {}) or {}
        server_d = data.get("server", {}) or {}
        categories_raw = data.get("categories")

        cfg = Config(
            version=int(data.get("version", defaults.version)),
            user=UserConfig(
                git_author_patterns=list(
                    user_d.get("git_author_patterns", defaults.user.git_author_patterns)
                )
            ),
            tracking=TrackingConfig(
                poll_interval_seconds=int(
                    tracking_d.get(
                        "poll_interval_seconds", defaults.tracking.poll_interval_seconds
                    )
                ),
                idle_threshold_seconds=int(
                    tracking_d.get(
                        "idle_threshold_seconds", defaults.tracking.idle_threshold_seconds
                    )
                ),
            ),
            git=GitConfig(
                scan_paths=list(git_d.get("scan_paths", defaults.git.scan_paths)),
                scan_depth=int(git_d.get("scan_depth", defaults.git.scan_depth)),
            ),
            calendar=CalendarConfig(
                ics_urls=list(cal_d.get("ics_urls", defaults.calendar.ics_urls)),
                cache_minutes=int(
                    cal_d.get("cache_minutes", defaults.calendar.cache_minutes)
                ),
                owner_email=str(cal_d.get("owner_email", defaults.calendar.owner_email)),
            ),
            categories=(
                [
                    CategoryRule(str(c["name"]), list(c.get("keywords", [])))
                    for c in categories_raw
                ]
                if categories_raw is not None
                else defaults.categories
            ),
            server=ServerConfig(
                host=str(server_d.get("host", defaults.server.host)),
                port=int(server_d.get("port", defaults.server.port)),
            ),
        )
    except (TypeError, ValueError, KeyError) as exc:
        raise ConfigError(f"config.json is malformed: {exc}") from exc

    _validate(cfg)
    return cfg


def _validate(cfg: Config) -> None:
    if cfg.server.port < 1 or cfg.server.port > 65535:
        raise ConfigError(f"server.port must be between 1 and 65535, got {cfg.server.port}")
    if cfg.server.host not in ("127.0.0.1", "localhost"):
        raise ConfigError(
            "server.host must be '127.0.0.1' or 'localhost' — daylog's web UI must never "
            f"be reachable from the network, got {cfg.server.host!r}"
        )
    if cfg.tracking.poll_interval_seconds <= 0:
        raise ConfigError("tracking.poll_interval_seconds must be greater than 0")
    if cfg.tracking.idle_threshold_seconds <= 0:
        raise ConfigError("tracking.idle_threshold_seconds must be greater than 0")
    if cfg.git.scan_depth < 0:
        raise ConfigError("git.scan_depth must be 0 or greater")
    if cfg.calendar.cache_minutes < 0:
        raise ConfigError("calendar.cache_minutes must be 0 or greater")
    if not cfg.categories:
        raise ConfigError("categories must contain at least one rule (e.g. a catch-all 'Other')")
    for rule in cfg.categories:
        if not rule.name:
            raise ConfigError("every category rule needs a non-empty 'name'")


def config_exists(path: Optional[Path] = None) -> bool:
    return (path or config_path()).exists()


def load_config(path: Optional[Path] = None) -> Config:
    p = path or config_path()
    if not p.exists():
        raise ConfigError(
            f"No config found at {p}. Run 'daylog init' to create one with sensible defaults."
        )
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config.json at {p} is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError(f"config.json at {p} must contain a JSON object at the top level")
    return _dict_to_cfg(raw)


def save_config(cfg: Config, path: Optional[Path] = None) -> Path:
    p = path or config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(_cfg_to_dict(cfg), indent=2) + "\n", encoding="utf-8")
    return p
