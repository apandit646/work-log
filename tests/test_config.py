import json

import pytest

from daylog.config import (
    Config,
    ConfigError,
    default_config,
    load_config,
    save_config,
)
from daylog.paths import config_path


def test_default_config_round_trips_through_disk(daylog_home):
    cfg = default_config()
    path = save_config(cfg)
    assert path == config_path()

    loaded = load_config()
    assert loaded == cfg


def test_load_missing_config_raises_with_actionable_message(daylog_home):
    with pytest.raises(ConfigError) as exc_info:
        load_config()
    assert "daylog init" in str(exc_info.value)


def test_load_invalid_json_raises_configerror(daylog_home):
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text("{not valid json", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config()


def test_load_non_object_json_raises_configerror(daylog_home):
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text("[1, 2, 3]", encoding="utf-8")

    with pytest.raises(ConfigError):
        load_config()


def test_partial_config_is_filled_in_with_defaults(daylog_home):
    config_path().parent.mkdir(parents=True, exist_ok=True)
    # Only set one nested field; everything else should come from defaults.
    config_path().write_text(json.dumps({"git": {"scan_depth": 2}}), encoding="utf-8")

    cfg = load_config()
    assert cfg.git.scan_depth == 2
    assert cfg.git.scan_paths == default_config().git.scan_paths
    assert cfg.server.port == 8765
    assert cfg.categories == default_config().categories


def test_invalid_port_is_rejected(daylog_home):
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps({"server": {"port": 99999}}), encoding="utf-8")

    with pytest.raises(ConfigError, match="port"):
        load_config()


def test_non_local_host_is_rejected(daylog_home):
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps({"server": {"host": "0.0.0.0"}}), encoding="utf-8")

    with pytest.raises(ConfigError, match="127.0.0.1|network"):
        load_config()


def test_empty_categories_is_rejected(daylog_home):
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps({"categories": []}), encoding="utf-8")

    with pytest.raises(ConfigError, match="categories"):
        load_config()


def test_llm_config_defaults_off(daylog_home):
    cfg = default_config()
    assert cfg.llm.enabled is False
    assert cfg.llm.model == "claude-opus-5"


def test_llm_config_round_trips(daylog_home):
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(
        json.dumps({"llm": {"enabled": True, "model": "claude-sonnet-5"}}), encoding="utf-8"
    )

    cfg = load_config()
    assert cfg.llm.enabled is True
    assert cfg.llm.model == "claude-sonnet-5"


def test_llm_enabled_with_empty_model_is_rejected(daylog_home):
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps({"llm": {"enabled": True, "model": ""}}), encoding="utf-8")

    with pytest.raises(ConfigError, match="llm.model"):
        load_config()


def test_llm_disabled_with_empty_model_is_still_valid(daylog_home):
    config_path().parent.mkdir(parents=True, exist_ok=True)
    config_path().write_text(json.dumps({"llm": {"enabled": False, "model": ""}}), encoding="utf-8")

    cfg = load_config()  # must not raise
    assert cfg.llm.enabled is False
