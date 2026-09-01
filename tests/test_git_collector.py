"""Git collector tests against real temporary repos created by each test
(no fixture .git bundles) — matches the "real tests, not smoke tests"
requirement for the git collector specifically."""
import os
import subprocess

import pytest

from daylog import storage
from daylog.collectors import git
from daylog.config import default_config


def _git(repo, *args, env=None):
    subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=env
    )


def make_repo(path, name):
    repo = path / name
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    return repo


def make_commit(
    repo,
    message,
    *,
    filename="file.txt",
    content="line1\n",
    when="2026-09-01T09:00:00",
    author_name="Me",
    author_email="me@example.com",
):
    (repo / filename).write_text(content, encoding="utf-8")
    _git(repo, "add", filename)
    env = os.environ.copy()
    date = f"{when} +0000"
    env.update(
        {
            "GIT_AUTHOR_DATE": date,
            "GIT_COMMITTER_DATE": date,
            "GIT_AUTHOR_NAME": author_name,
            "GIT_AUTHOR_EMAIL": author_email,
            "GIT_COMMITTER_NAME": author_name,
            "GIT_COMMITTER_EMAIL": author_email,
        }
    )
    _git(repo, "commit", "-q", "-m", message, env=env)


def make_config(scan_paths, patterns=None, depth=6):
    cfg = default_config()
    cfg.git.scan_paths = [str(p) for p in scan_paths]
    cfg.git.scan_depth = depth
    cfg.user.git_author_patterns = patterns or []
    return cfg


# --- discovery ---------------------------------------------------------


def test_discover_repos_finds_a_repo_and_does_not_descend_into_it(tmp_path):
    outer = make_repo(tmp_path, "outer")
    make_commit(outer, "init")
    # A nested .git inside the already-found repo (e.g. a submodule) must
    # not produce a second, separate discovery.
    (outer / "vendor").mkdir()
    make_repo(outer / "vendor", "lib")

    repos = git.discover_repos([str(tmp_path)], max_depth=6)
    assert repos == [outer]


def test_discover_repos_respects_bounded_depth(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d" / "e"
    deep.mkdir(parents=True)
    make_repo(deep, "repo")

    assert git.discover_repos([str(tmp_path)], max_depth=2) == []
    found = git.discover_repos([str(tmp_path)], max_depth=10)
    assert len(found) == 1


def test_discover_repos_skips_nonexistent_path():
    assert git.discover_repos(["/no/such/path/at/all"], max_depth=4) == []


def test_discover_repos_skips_unreadable_directory(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    inner = blocked / "inner"
    inner.mkdir()
    make_repo(inner, "repo")
    os.chmod(blocked, 0o000)
    try:
        if os.access(blocked, os.R_OK):
            pytest.skip("current user bypasses permission bits (e.g. root)")
        assert git.discover_repos([str(tmp_path)], max_depth=6) == []
    finally:
        os.chmod(blocked, 0o755)


def test_discover_repos_finds_multiple_sibling_repos(tmp_path):
    a = make_repo(tmp_path, "repo-a")
    b = make_repo(tmp_path, "repo-b")
    found = set(git.discover_repos([str(tmp_path)], max_depth=6))
    assert found == {a, b}


# --- commit collection ---------------------------------------------------


def test_collects_todays_commits_filtered_by_author_with_line_counts(tmp_path):
    repo = make_repo(tmp_path, "proj")
    make_commit(repo, "initial", content="line1\n", when="2026-08-31T09:00:00")
    make_commit(
        repo, "add feature", content="line1\nline2\nline3\n", when="2026-09-01T10:00:00"
    )
    make_commit(
        repo, "by someone else", when="2026-09-01T11:00:00",
        author_name="Other", author_email="other@example.com", filename="other.txt",
    )

    cfg = make_config([tmp_path], patterns=["me@example.com"])
    result = git.collect(cfg, "2026-09-01")

    assert result.available is True
    assert len(result.repos) == 1
    commits = result.repos[0].commits
    assert [c.subject for c in commits] == ["add feature"]
    assert commits[0].additions == 2
    assert commits[0].deletions == 0
    assert commits[0].branch == "main"
    assert commits[0].repo == "proj"


def test_falls_back_to_repo_git_config_when_no_patterns_configured(tmp_path):
    repo = make_repo(tmp_path, "proj")
    _git(repo, "config", "user.name", "Local Identity")
    _git(repo, "config", "user.email", "local@example.com")
    make_commit(
        repo, "work", when="2026-09-01T09:00:00",
        author_name="Local Identity", author_email="local@example.com",
    )

    cfg = make_config([tmp_path], patterns=[])
    result = git.collect(cfg, "2026-09-01")

    assert [c.subject for c in result.repos[0].commits] == ["work"]


def test_no_identity_available_yields_no_commits_without_crashing(tmp_path):
    repo = make_repo(tmp_path, "proj")
    make_commit(repo, "work", when="2026-09-01T09:00:00")  # local config left unset

    cfg = make_config([tmp_path], patterns=[])
    result = git.collect(cfg, "2026-09-01")

    assert result.available is True
    assert result.repos[0].commits == []
    assert result.repos[0].error is None


def test_repo_with_no_commits_yet_is_handled(tmp_path):
    make_repo(tmp_path, "empty-proj")
    cfg = make_config([tmp_path], patterns=["me@example.com"])

    result = git.collect(cfg, "2026-09-01")

    assert result.available is True
    assert result.repos[0].commits == []
    assert result.repos[0].error is None


def test_detached_head_commits_are_collected(tmp_path):
    repo = make_repo(tmp_path, "proj")
    make_commit(repo, "first", when="2026-09-01T09:00:00")
    commit_hash = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()
    _git(repo, "checkout", "-q", "--detach", commit_hash)

    cfg = make_config([tmp_path], patterns=["me@example.com"])
    result = git.collect(cfg, "2026-09-01")

    commits = result.repos[0].commits
    assert [c.subject for c in commits] == ["first"]
    assert commits[0].branch == "(detached HEAD)"


def test_commits_across_branches_are_deduplicated_and_attributed(tmp_path):
    repo = make_repo(tmp_path, "proj")
    make_commit(repo, "base work", when="2026-09-01T09:00:00")
    _git(repo, "checkout", "-q", "-b", "feature")
    make_commit(repo, "feature work", filename="f2.txt", when="2026-09-01T10:00:00")
    _git(repo, "checkout", "-q", "main")

    cfg = make_config([tmp_path], patterns=["me@example.com"])
    result = git.collect(cfg, "2026-09-01")

    commits = result.repos[0].commits
    assert len(commits) == 2  # the shared base commit is not double-counted
    by_subject = {c.subject: c.branch for c in commits}
    assert by_subject == {"base work": "main", "feature work": "feature"}


def test_commits_outside_the_requested_day_are_excluded(tmp_path):
    repo = make_repo(tmp_path, "proj")
    make_commit(repo, "yesterday", when="2026-08-31T23:00:00")
    make_commit(repo, "tomorrow", filename="f2.txt", when="2026-09-02T01:00:00")

    cfg = make_config([tmp_path], patterns=["me@example.com"])
    result = git.collect(cfg, "2026-09-01")

    assert result.repos[0].commits == []


# --- uncommitted work in progress ----------------------------------------


def test_uncommitted_changes_are_listed(tmp_path):
    repo = make_repo(tmp_path, "proj")
    make_commit(repo, "initial", filename="tracked.txt", when="2026-09-01T09:00:00")

    (repo / "tracked.txt").write_text("changed\n", encoding="utf-8")
    (repo / "new_untracked.txt").write_text("new\n", encoding="utf-8")
    (repo / "new_staged.txt").write_text("staged\n", encoding="utf-8")
    _git(repo, "add", "new_staged.txt")

    cfg = make_config([tmp_path], patterns=["me@example.com"])
    result = git.collect(cfg, "2026-09-01")

    wip = {f["path"]: f["status"] for f in result.repos[0].wip}
    assert wip == {"tracked.txt": "M", "new_untracked.txt": "??", "new_staged.txt": "A"}


def test_clean_repo_has_no_wip(tmp_path):
    repo = make_repo(tmp_path, "proj")
    make_commit(repo, "initial", when="2026-09-01T09:00:00")

    cfg = make_config([tmp_path], patterns=["me@example.com"])
    result = git.collect(cfg, "2026-09-01")

    assert result.repos[0].wip == []


# --- git missing / integration with storage -------------------------------


def test_git_missing_marks_collection_unavailable(monkeypatch):
    monkeypatch.setattr(git.shutil, "which", lambda name: None)
    result = git.collect(default_config(), "2026-09-01")
    assert result.available is False
    assert result.error is not None
    assert result.repos == []


def test_cache_row_shapes_round_trip_through_storage(tmp_path, daylog_home):
    repo = make_repo(tmp_path, "proj")
    make_commit(repo, "add feature", when="2026-09-01T09:00:00")
    (repo / "scratch.txt").write_text("wip\n", encoding="utf-8")

    cfg = make_config([tmp_path], patterns=["me@example.com"])
    result = git.collect(cfg, "2026-09-01")

    with storage.open_db() as conn:
        storage.replace_commits_cache(conn, "2026-09-01", git.commits_to_cache_rows(result))
        storage.replace_wip_cache(conn, "2026-09-01", git.wip_to_cache_rows(result))

        commits = storage.get_commits(conn, "2026-09-01")
        assert len(commits) == 1
        assert commits[0]["subject"] == "add feature"
        assert commits[0]["repo"] == "proj"

        wip = storage.get_wip(conn, "2026-09-01")
        assert len(wip) == 1
        assert wip[0]["repo"] == "proj"
        assert wip[0]["files"][0]["path"] == "scratch.txt"
