from daylog.categorize import categorize
from daylog.config import CategoryRule

RULES = [
    CategoryRule("Meetings", ["teams", "zoom"]),
    CategoryRule("Coding", ["code", "vim"]),
    CategoryRule("Other", []),
]


def test_matches_app_name():
    assert categorize("Zoom", "Weekly sync", RULES) == "Meetings"


def test_matches_window_title_when_app_name_does_not():
    assert categorize("chrome", "Reviewing PR in vim keybindings", RULES) == "Coding"


def test_first_rule_wins_when_multiple_match():
    rules = [CategoryRule("A", ["code"]), CategoryRule("B", ["code"])]
    assert categorize("Code", "some title", rules) == "A"


def test_case_insensitive():
    assert categorize("TEAMS", "STANDUP", RULES) == "Meetings"


def test_unmatched_falls_through_to_catchall():
    assert categorize("Finder", "Downloads", RULES) == "Other"


def test_empty_rules_returns_other():
    assert categorize("anything", "anything", []) == "Other"
