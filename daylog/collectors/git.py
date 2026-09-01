"""Git collector: discovers repos under configured directories and collects
today's commits (across all local branches, deduplicated, filtered to a
configured author identity) plus uncommitted work in progress.

Everything shells out to the `git` binary via subprocess — no GitPython
dependency, matching the stdlib-only core. Nothing here raises for an
ordinary "no data" condition: git missing, no commits today, a detached
HEAD, an empty repo, or an unreadable directory all produce an
empty/partial result, never an exception — collect() catches per-repo
failures so one bad repo doesn't take down the whole collection, and
reports git-missing as `available=False` for the caller (the report
builder, Phase 5) to mark the section unavailable rather than crash.
"""
from __future__ import annotations

import dataclasses
import datetime as _dt
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

if TYPE_CHECKING:
    from ..config import Config

_TIMEOUT = 10
_FIELD_SEP = "\x1f"
_COMMIT_MARK = "\x02"
_PRETTY = f"{_COMMIT_MARK}%H{_FIELD_SEP}%aI{_FIELD_SEP}%an{_FIELD_SEP}%ae{_FIELD_SEP}%s"


def git_available() -> bool:
    return shutil.which("git") is not None


@dataclasses.dataclass
class Commit:
    repo: str
    hash: str
    subject: str
    branch: Optional[str]
    timestamp: str
    additions: int
    deletions: int

    def to_dict(self) -> Dict[str, Any]:
        return dataclasses.asdict(self)


@dataclasses.dataclass
class RepoResult:
    name: str
    path: str
    commits: List[Commit] = dataclasses.field(default_factory=list)
    wip: List[Dict[str, str]] = dataclasses.field(default_factory=list)
    error: Optional[str] = None  # set only if *this* repo couldn't be read


@dataclasses.dataclass
class GitCollection:
    available: bool
    error: Optional[str] = None
    repos: List[RepoResult] = dataclasses.field(default_factory=list)

    @property
    def commits(self) -> List[Commit]:
        return [c for r in self.repos for c in r.commits]


def commits_to_cache_rows(collection: GitCollection) -> List[Dict[str, Any]]:
    """Shape expected by storage.replace_commits_cache()."""
    return [c.to_dict() for c in collection.commits]


def wip_to_cache_rows(collection: GitCollection) -> List[Dict[str, Any]]:
    """Shape expected by storage.replace_wip_cache()."""
    return [{"repo": r.name, "files": r.wip} for r in collection.repos if r.wip]


def _run(repo_path: Path, args: List[str]) -> Optional[str]:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            capture_output=True,
            text=True,
            timeout=_TIMEOUT,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout


def discover_repos(scan_paths: List[str], max_depth: int) -> List[Path]:
    """Walks each configured path up to max_depth levels, stopping (not
    descending further) as soon as a `.git` is found. A path that doesn't
    exist, or a directory we can't read, is skipped rather than an error."""
    found: List[Path] = []
    seen: set = set()
    for raw in scan_paths:
        root = Path(raw).expanduser()
        if not root.is_dir():
            continue
        _walk(root, max_depth, found, seen)
    return found


def _walk(path: Path, depth_left: int, found: List[Path], seen: set) -> None:
    if (path / ".git").exists():
        resolved = path.resolve()
        if resolved not in seen:
            seen.add(resolved)
            found.append(path)
        return  # a repo's internals aren't more repos to scan into

    if depth_left <= 0:
        return
    try:
        children = [c for c in path.iterdir() if c.is_dir() and not c.name.startswith(".")]
    except OSError:
        return  # permission denied or similar — just skip this subtree
    for child in children:
        _walk(child, depth_left - 1, found, seen)


def _repo_identity(repo_path: Path) -> List[str]:
    """Fallback author patterns from this repo's own `git config`, used
    when config.user.git_author_patterns is empty."""
    patterns = []
    name = _run(repo_path, ["config", "user.name"])
    email = _run(repo_path, ["config", "user.email"])
    if name and name.strip():
        patterns.append(name.strip())
    if email and email.strip():
        patterns.append(email.strip())
    return patterns


def _matches_author(author_name: str, author_email: str, patterns: List[str]) -> bool:
    haystack = f"{author_name} <{author_email}>".lower()
    return any(p.lower() in haystack for p in patterns)


def _current_branch(repo_path: Path) -> Optional[str]:
    out = _run(repo_path, ["rev-parse", "--abbrev-ref", "HEAD"])
    if out is None:
        return None
    branch = out.strip()
    return None if branch == "HEAD" else branch  # "HEAD" means detached


def _local_branches(repo_path: Path) -> List[str]:
    out = _run(repo_path, ["for-each-ref", "refs/heads", "--format=%(refname:short)"])
    if not out:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def _shift_day(day: str, delta_days: int) -> str:
    return (_dt.date.fromisoformat(day) + _dt.timedelta(days=delta_days)).isoformat()


def _log_commits(repo_path: Path, ref: str, day: str) -> List[Dict[str, Any]]:
    """Raw, not-yet-author-filtered commits reachable from `ref` whose
    author date falls on `day`.

    --since/--until (padded a day either side) is just a cheap traversal
    bound — it filters on commit date, which can differ from author date
    after a rebase — so the authoritative filter is done in Python below
    against the parsed %aI author-date field.
    """
    out = _run(
        repo_path,
        [
            "log",
            ref,
            f"--since={_shift_day(day, -1)} 00:00:00",
            f"--until={_shift_day(day, 1)} 00:00:00",
            "--numstat",
            f"--pretty=format:{_PRETTY}",
        ],
    )
    if not out:
        return []

    commits: List[Dict[str, Any]] = []
    current: Optional[Dict[str, Any]] = None
    for line in out.splitlines():
        if line.startswith(_COMMIT_MARK):
            if current is not None:
                commits.append(current)
            fields = line[len(_COMMIT_MARK):].split(_FIELD_SEP)
            if len(fields) != 5:
                current = None
                continue
            hash_, timestamp, author_name, author_email, subject = fields
            current = {
                "hash": hash_,
                "timestamp": timestamp,
                "author_name": author_name,
                "author_email": author_email,
                "subject": subject,
                "additions": 0,
                "deletions": 0,
            }
        elif line.strip() and current is not None:
            parts = line.split("\t")
            if len(parts) == 3:
                added, deleted, _path = parts
                current["additions"] += int(added) if added.isdigit() else 0
                current["deletions"] += int(deleted) if deleted.isdigit() else 0
    if current is not None:
        commits.append(current)

    return [c for c in commits if c["timestamp"][:10] == day]


def _collect_repo_commits(repo_path: Path, day: str, patterns: List[str]) -> List[Commit]:
    repo_name = repo_path.name
    refs: List[Tuple[str, str]] = []

    current = _current_branch(repo_path)
    if current is None:
        refs.append(("HEAD", "(detached HEAD)"))
    else:
        refs.append((current, current))
    for branch in _local_branches(repo_path):
        if branch != current:
            refs.append((branch, branch))

    seen_hashes: set = set()
    commits: List[Commit] = []
    for ref, label in refs:
        for raw in _log_commits(repo_path, ref, day):
            if raw["hash"] in seen_hashes:
                continue
            if not _matches_author(raw["author_name"], raw["author_email"], patterns):
                continue
            seen_hashes.add(raw["hash"])
            commits.append(
                Commit(
                    repo=repo_name,
                    hash=raw["hash"],
                    subject=raw["subject"],
                    branch=label,
                    timestamp=raw["timestamp"],
                    additions=raw["additions"],
                    deletions=raw["deletions"],
                )
            )
    commits.sort(key=lambda c: c.timestamp)
    return commits


def _collect_wip(repo_path: Path) -> List[Dict[str, str]]:
    out = _run(repo_path, ["status", "--porcelain=v1"])
    if not out:
        return []
    files = []
    for line in out.splitlines():
        if not line:
            continue
        status = line[:2].strip()
        path = line[3:]
        if " -> " in path:  # rename: "old -> new" — keep the destination
            path = path.split(" -> ", 1)[1]
        files.append({"path": path, "status": status or "?"})
    return files


def collect(config: "Config", day: str) -> GitCollection:
    if not git_available():
        return GitCollection(available=False, error="git is not installed or not on PATH")

    repo_paths = discover_repos(config.git.scan_paths, config.git.scan_depth)
    results: List[RepoResult] = []
    for repo_path in repo_paths:
        name = repo_path.name
        try:
            patterns = list(config.user.git_author_patterns) or _repo_identity(repo_path)
            if not patterns:
                # No identity configured, and this repo's own git config has
                # none either — nothing to filter "my commits" by.
                results.append(RepoResult(name=name, path=str(repo_path)))
                continue
            commits = _collect_repo_commits(repo_path, day, patterns)
            wip = _collect_wip(repo_path)
            results.append(RepoResult(name=name, path=str(repo_path), commits=commits, wip=wip))
        except Exception as exc:  # pragma: no cover - defensive, mirrors doc'd guarantee
            results.append(RepoResult(name=name, path=str(repo_path), error=str(exc)))

    return GitCollection(available=True, repos=results)
