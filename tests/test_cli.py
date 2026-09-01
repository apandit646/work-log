import json

from daylog.cli import main
from daylog.paths import config_path, db_path


def test_init_creates_config_and_database(daylog_home, capsys):
    exit_code = main(["init"])
    assert exit_code == 0
    assert config_path().exists()
    assert db_path().exists()

    cfg = json.loads(config_path().read_text(encoding="utf-8"))
    assert cfg["server"]["host"] == "127.0.0.1"

    out = capsys.readouterr().out
    assert "Wrote default config" in out


def test_init_without_force_does_not_overwrite_existing_config(daylog_home, capsys):
    main(["init"])
    config_path().write_text('{"server": {"port": 9999}}', encoding="utf-8")

    main(["init"])  # no --force

    cfg = json.loads(config_path().read_text(encoding="utf-8"))
    assert cfg["server"]["port"] == 9999


def test_init_with_force_overwrites_existing_config(daylog_home):
    main(["init"])
    config_path().write_text('{"server": {"port": 9999}}', encoding="utf-8")

    main(["init", "--force"])

    cfg = json.loads(config_path().read_text(encoding="utf-8"))
    assert cfg["server"]["port"] == 8765


def test_doctor_runs_without_crashing_before_init(daylog_home, capsys):
    exit_code = main(["doctor"])
    # No config yet, so doctor must report FAIL (not raise) and exit non-zero.
    assert exit_code == 1
    out = capsys.readouterr().out
    assert "config.json valid" in out
    assert "FAIL" in out


def test_doctor_runs_after_init(daylog_home, capsys):
    main(["init"])
    exit_code = main(["doctor"])
    out = capsys.readouterr().out
    assert "config.json valid" in out
    assert "database writable" in out
    assert exit_code in (0, 1)  # environment-dependent (e.g. missing xdotool in CI)


def test_unimplemented_subcommands_exit_cleanly(daylog_home, capsys):
    for args in (["track"], ["report"], ["status"], ["ui"]):
        exit_code = main(args)
        assert exit_code == 1
        out = capsys.readouterr().out
        assert "isn't implemented yet" in out
