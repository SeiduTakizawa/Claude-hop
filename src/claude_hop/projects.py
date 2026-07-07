"""Project-store inspection.

Encoded directory names are lossy (every non-alphanumeric became a dash),
so the real project path is recovered from the ``cwd`` fields Claude Code
writes into the session JSONL — and only trusted when it round-trips:
``encode(cwd) == directory name``. An unvalidated cwd is never used to
build mapping suggestions.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections.abc import Iterable
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

from claude_hop.remap import PathMapper, encode_path

_CWD_SCAN_LINES = 25  # per session file


def recover_path(project_dir: Path) -> tuple[str | None, bool]:
    """Recover the project's real path from its session files.

    Returns ``(path, ambiguous)``:
    - ``(path, False)``  — a cwd was found and encode(cwd) matches the dir
    - ``(None, True)``   — cwd(s) found but none match the dir name
    - ``(None, False)``  — no parseable cwd anywhere
    """
    saw_cwd = False
    for session in sorted(project_dir.glob("*.jsonl")):
        try:
            with open(session, encoding="utf-8", errors="replace") as f:
                for i, line in enumerate(f):
                    if i >= _CWD_SCAN_LINES:
                        break
                    try:
                        cwd = json.loads(line).get("cwd")
                    except (json.JSONDecodeError, AttributeError):
                        continue
                    if not isinstance(cwd, str) or not cwd:
                        continue
                    saw_cwd = True
                    if encode_path(cwd) == project_dir.name:
                        return cwd, False
        except OSError:
            continue
    return None, saw_cwd


def is_outside_home(name: str, local_home: str | Path, mapper: PathMapper) -> bool:
    """True when the home remap can't place this project: the mapper leaves
    the name unchanged and the name isn't under the encoded local home."""
    if mapper.remap_dirname(name) != name:
        return False
    home_enc = encode_path(local_home)
    return not (name == home_enc or name.startswith(home_enc + "-"))


@dataclass
class OutsideReport:
    validated: dict[str, str] = field(default_factory=dict)  # name -> real path
    undetermined: list[str] = field(default_factory=list)  # no parseable cwd
    ambiguous: list[str] = field(default_factory=list)  # cwd doesn't match name

    def __bool__(self) -> bool:
        return bool(self.validated or self.undetermined or self.ambiguous)

    @property
    def names(self) -> list[str]:
        return sorted([*self.validated, *self.undetermined, *self.ambiguous])


def find_outside_home(
    projects_dir: Path,
    local_home: str | Path,
    mapper: PathMapper,
    only: set[str] | None = None,
) -> OutsideReport:
    report = OutsideReport()
    if not projects_dir.is_dir():
        return report
    for proj in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        if only is not None and proj.name not in only:
            continue
        if not is_outside_home(proj.name, local_home, mapper):
            continue
        path, ambiguous = recover_path(proj)
        if path is not None:
            report.validated[proj.name] = path
        elif ambiguous:
            report.ambiguous.append(proj.name)
        else:
            report.undetermined.append(proj.name)
    return report


def cluster_prefixes(paths: Iterable[str]) -> list[str]:
    """Common path prefixes for mapping suggestions: group by the first two
    path components (``/mnt/c``, ``/opt``, …), commonpath within a group —
    disjoint groups yield multiple suggestions."""
    groups: dict[str, list[str]] = {}
    for p in paths:
        parts = PurePosixPath(p).parts
        anchor = "/".join(parts[:3]) if len(parts) >= 3 else p
        groups.setdefault(anchor, []).append(p)
    return sorted(os.path.commonpath(group) for group in groups.values())


def mapping_snippet(report: OutsideReport, remote_name: str, remote_home: str) -> str:
    """A ready-to-paste [remotes.<name>.mappings] block from VALIDATED paths
    only. Targets land under the remote home (a target equal to the home
    itself would be rejected: pull couldn't invert it)."""
    lines = [f"[remotes.{remote_name}.mappings]"]
    for prefix in cluster_prefixes(report.validated.values()):
        target = f"{remote_home.rstrip('/')}/{os.path.basename(prefix)}"
        lines.append(f'"{prefix}" = "{target}"')
    return "\n".join(lines)


# ------------------------------------------------------------------- git


@dataclass
class GitState:
    path: str
    dirty: bool
    ahead: int | None  # None = no upstream configured

    @property
    def noteworthy(self) -> bool:
        return self.dirty or self.ahead is None or self.ahead > 0


def _git(path: str, *args: str) -> subprocess.CompletedProcess[str] | None:
    try:
        return subprocess.run(
            ["git", "-C", path, *args], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def git_state(path: str) -> GitState | None:
    """Dirty/ahead state of the repo at ``path``; None (silently) when git
    is missing, the path doesn't exist, or it isn't a work tree."""
    if not shutil.which("git") or not Path(path).is_dir():
        return None
    probe = _git(path, "rev-parse", "--is-inside-work-tree")
    if probe is None or probe.returncode != 0 or probe.stdout.strip() != "true":
        return None
    status = _git(path, "status", "--porcelain")
    dirty = bool(status and status.stdout.strip())
    ahead_run = _git(path, "rev-list", "--count", "@{u}..HEAD")
    ahead: int | None = None
    if ahead_run is not None and ahead_run.returncode == 0:
        try:
            ahead = int(ahead_run.stdout.strip())
        except ValueError:
            ahead = None
    return GitState(path=path, dirty=dirty, ahead=ahead)


def git_warnings(
    projects_dir: Path, only: set[str] | None = None
) -> list[tuple[str, GitState]]:
    """(project name, state) for synced projects whose repos have work that
    hasn't traveled via git. Unrecoverable paths and non-repos are skipped
    silently — advisory only, zero noise."""
    warnings = []
    if not projects_dir.is_dir() or not shutil.which("git"):
        return warnings
    for proj in sorted(p for p in projects_dir.iterdir() if p.is_dir()):
        if only is not None and proj.name not in only:
            continue
        path, _ambiguous = recover_path(proj)
        if path is None:
            continue
        state = git_state(path)
        if state is not None and state.noteworthy:
            warnings.append((proj.name, state))
    return warnings
