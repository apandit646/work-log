from daylog import doctor


def test_run_checks_returns_structured_results(daylog_home):
    results = doctor.run_checks()
    assert results  # never empty
    for r in results:
        assert set(r.keys()) == {"label", "ok", "detail", "optional"}
        assert isinstance(r["ok"], bool)


def test_run_checks_never_raises_without_config(daylog_home):
    results = doctor.run_checks()
    by_label = {r["label"]: r for r in results}
    assert by_label["config.json valid"]["ok"] is False


def test_all_required_ok_ignores_optional_checks():
    results = [
        {"label": "a", "ok": True, "detail": "", "optional": False},
        {"label": "b", "ok": False, "detail": "", "optional": True},
    ]
    assert doctor.all_required_ok(results) is True

    results[0]["ok"] = False
    assert doctor.all_required_ok(results) is False
