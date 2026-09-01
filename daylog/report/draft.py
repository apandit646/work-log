"""Turns commits and meetings into the "Draft for the timesheet" block:
one plain, past-tense line per real unit of work.

This is deliberately a mechanical, rule-based transformer — a small
jargon dictionary plus a leading-verb tense normalizer — not genuine
rewriting. It has no way to know that "the parser" means "the invoice
parser"; it can only work with what's literally in the commit subject.
The optional Phase 9 LLM polish (off by default) is what would get closer
to true business-language rewriting; this module's job is a solid,
honest, zero-cost, evidence-only baseline.

Never invents work: every line traces back to an actual commit or an
actual meeting over 15 minutes. No commits/meetings -> no lines.
"""
from __future__ import annotations

import re
from typing import List, Optional

from .types import MeetingInfo, RepoCommits

_MIN_MEETING_MINUTES = 15

_TRIVIAL_RE = re.compile(
    r"^(wip\b|typo\b|fix(ed)?\s+typo|formatting\b|format\b|lint(ing)?\b|"
    r"merge\b|merge branch|merge pull request)",
    re.IGNORECASE,
)

_PREFIX_RE = re.compile(
    r"^(feat|fix|chore|refactor|docs|test|perf|build|ci|style)(\([^)]*\))?:\s*", re.IGNORECASE
)

# A small, honest set of common technical shorthand -> plain phrasing.
# Not exhaustive by design; see module docstring.
_JARGON = [
    (re.compile(r"\bnull\s*ptr\b|\bnullptr\b|\bnpe\b|\bnull pointer\b", re.I), "a crash"),
    (re.compile(r"\brace condition\b", re.I), "a timing bug"),
    (re.compile(r"\bregression\b", re.I), "a bug"),
    (re.compile(r"\bexception\b", re.I), "an error"),
    (re.compile(r"\bmemory leak\b", re.I), "a memory leak"),
]

_VERB_MAP = {
    "fix": "Fixed", "fixed": "Fixed", "fixes": "Fixed",
    "add": "Added", "added": "Added", "adds": "Added",
    "update": "Updated", "updated": "Updated", "updates": "Updated",
    "remove": "Removed", "removed": "Removed", "removes": "Removed",
    "delete": "Removed", "deleted": "Removed",
    "refactor": "Refactored", "refactored": "Refactored",
    "implement": "Implemented", "implemented": "Implemented",
    "improve": "Improved", "improved": "Improved",
    "optimize": "Optimized", "optimized": "Optimized",
    "rewrite": "Rewrote", "rewrote": "Rewrote",
    "migrate": "Migrated", "migrated": "Migrated",
    "clean": "Cleaned up", "cleanup": "Cleaned up", "cleaned": "Cleaned up",
    "support": "Added support for",
}

# A conventional-commit prefix (e.g. "fix:") already tells us the verb —
# stripping the prefix without remembering that would leave a sentence
# with no verb at all ("fix: null pointer" -> "null pointer", not
# "Fixed null pointer").
_PREFIX_VERB = {
    "feat": "Added", "fix": "Fixed", "chore": "Updated", "refactor": "Refactored",
    "docs": "Updated", "test": "Updated", "perf": "Optimized", "build": "Updated",
    "ci": "Updated", "style": "Updated",
}


def is_trivial(subject: str) -> bool:
    subject = subject.strip()
    if len(subject) < 4:
        return True
    return bool(_TRIVIAL_RE.match(subject))


def normalize_subject(subject: str) -> str:
    """Best-effort plain-language rewrite of one commit subject."""
    subject = subject.strip()
    prefix_match = _PREFIX_RE.match(subject)
    implied_verb = _PREFIX_VERB.get(prefix_match.group(1).lower()) if prefix_match else None
    text = subject[prefix_match.end():] if prefix_match else subject

    for pattern, replacement in _JARGON:
        text = pattern.sub(replacement, text)

    lead, _, rest = text.partition(" ")
    lead_key = lead.lower().strip(":,.")
    if lead_key in _VERB_MAP:
        text = f"{_VERB_MAP[lead_key]} {rest}".strip()
    elif implied_verb:
        text = f"{implied_verb} {text}".strip()

    return text[:1].upper() + text[1:] if text else text


def _repo_line(repo_commits: RepoCommits) -> Optional[str]:
    kept = [c for c in repo_commits.commits if not is_trivial(c.subject)]
    if not kept:
        return None
    phrases = [normalize_subject(c.subject) for c in kept]
    if len(phrases) == 1:
        return f"{phrases[0]} in {repo_commits.repo}."
    lowered = [phrases[0]] + [p[:1].lower() + p[1:] for p in phrases[1:]]
    return f"Worked on {repo_commits.repo}: " + "; ".join(lowered) + "."


def _meeting_line(meeting: MeetingInfo) -> Optional[str]:
    if meeting.minutes <= _MIN_MEETING_MINUTES:
        return None
    return f"Attended {meeting.title}."


def build_draft(commits_by_repo: List[RepoCommits], meetings: List[MeetingInfo]) -> List[str]:
    lines: List[str] = []
    for repo_commits in commits_by_repo:
        line = _repo_line(repo_commits)
        if line:
            lines.append(line)
    for meeting in sorted(meetings, key=lambda m: m.start):
        line = _meeting_line(meeting)
        if line:
            lines.append(line)
    return lines
