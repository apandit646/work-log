import datetime as dt

from daylog.report import draft
from daylog.report.types import CommitInfo, MeetingInfo, RepoCommits


def _commit(subject, hash_="abc1234", additions=1, deletions=0, branch="main"):
    return CommitInfo(
        hash=hash_, subject=subject, branch=branch, timestamp="2026-09-01T09:00:00+00:00",
        additions=additions, deletions=deletions,
    )


def _meeting(title, minutes, source="work"):
    start = dt.datetime(2026, 9, 1, 9, 0, tzinfo=dt.timezone.utc)
    return MeetingInfo(title=title, start=start, end=start + dt.timedelta(minutes=minutes), calendar_source=source)


# --- is_trivial / normalize_subject -----------------------------------------


def test_trivial_commits_are_detected():
    for subject in ["wip", "typo", "fix typo in readme", "formatting", "lint fixes", "Merge branch 'main'"]:
        assert draft.is_trivial(subject), subject


def test_non_trivial_commit_is_not_flagged():
    assert draft.is_trivial("add retry logic to the upload client") is False


def test_normalize_strips_conventional_commit_prefix():
    assert draft.normalize_subject("fix: null pointer in parser") == "Fixed a crash in parser"


def test_normalize_maps_leading_verb_to_past_tense():
    assert draft.normalize_subject("add retry logic") == "Added retry logic"
    assert draft.normalize_subject("refactor the upload client") == "Refactored the upload client"


def test_normalize_applies_jargon_dictionary():
    assert "a crash" in draft.normalize_subject("fix null ptr in parser")
    assert "a timing bug" in draft.normalize_subject("fix race condition in scheduler")


def test_normalize_maps_a_second_known_verb():
    assert draft.normalize_subject("improve reliability of the sync job") == "Improved reliability of the sync job"


def test_normalize_just_capitalizes_when_no_known_verb():
    assert draft.normalize_subject("bump dependency versions") == "Bump dependency versions"


# --- build_draft: merging, filtering, evidence-only -------------------------


def test_single_commit_produces_one_line_naming_the_repo():
    repo = RepoCommits(repo="invoice-service", commits=[_commit("fix null ptr in parser")])
    lines = draft.build_draft([repo], [])
    assert lines == ["Fixed a crash in parser in invoice-service."]


def test_multiple_commits_in_one_repo_merge_into_one_line():
    repo = RepoCommits(
        repo="invoice-service",
        commits=[_commit("fix null ptr in parser"), _commit("add retry logic"), _commit("improve logging")],
    )
    lines = draft.build_draft([repo], [])
    assert len(lines) == 1
    assert lines[0].startswith("Worked on invoice-service:")
    assert "Fixed a crash in parser" in lines[0]
    assert "added retry logic" in lines[0]
    assert "improved logging" in lines[0]


def test_trivial_commits_are_excluded_from_the_draft():
    repo = RepoCommits(
        repo="invoice-service",
        commits=[_commit("wip"), _commit("fix typo"), _commit("Merge branch 'main'")],
    )
    assert draft.build_draft([repo], []) == []


def test_repo_with_only_trivial_commits_produces_no_line_even_with_other_repos():
    trivial_repo = RepoCommits(repo="scratch", commits=[_commit("wip")])
    real_repo = RepoCommits(repo="invoice-service", commits=[_commit("add retry logic")])
    lines = draft.build_draft([trivial_repo, real_repo], [])
    assert lines == ["Added retry logic in invoice-service."]


def test_meetings_over_15_minutes_are_included():
    lines = draft.build_draft([], [_meeting("Roadmap review", 30)])
    assert lines == ["Attended Roadmap review."]


def test_meetings_at_or_under_15_minutes_are_excluded():
    lines = draft.build_draft([], [_meeting("Quick check-in", 15)])
    assert lines == []
    lines = draft.build_draft([], [_meeting("Quick check-in", 5)])
    assert lines == []


def test_no_evidence_produces_no_lines():
    assert draft.build_draft([], []) == []


def test_meetings_are_ordered_by_start_time():
    early = _meeting("Standup", 20)
    late_start = dt.datetime(2026, 9, 1, 15, 0, tzinfo=dt.timezone.utc)
    late = MeetingInfo(title="Retro", start=late_start, end=late_start + dt.timedelta(minutes=30), calendar_source=None)
    lines = draft.build_draft([], [late, early])
    assert lines == ["Attended Standup.", "Attended Retro."]
