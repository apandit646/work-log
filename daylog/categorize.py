"""Keyword-based categorization, shared by the tracker (writes category
onto each activity_block as it's recorded) and the report builder (Phase 5,
which re-uses this for anything not already categorized).

First matching rule wins; a rule with an empty keyword list (the
conventional catch-all, e.g. "Other") matches nothing directly but is
still returned if nothing else matched — that's what makes it the
catch-all when it's last in config.categories.
"""
from __future__ import annotations

from typing import Sequence

from .config import CategoryRule


def categorize(app: str, title: str, rules: Sequence[CategoryRule]) -> str:
    if not rules:
        return "Other"
    haystack = f"{app} {title}".lower()
    for rule in rules:
        for keyword in rule.keywords:
            if keyword.lower() in haystack:
                return rule.name
    return rules[-1].name
