"""Push/pull orchestration and the safety invariants.

Invariants enforced here, not in the CLI:
- ``~/.claude/projects`` is never mutated in place — remapping happens in a
  staging temp dir, and the only writes to real session dirs go through
  ``rsync -u`` (newer file wins, never deletes).
- Refuse to sync while Claude Code is running (it flushes sessions to disk
  on exit), unless ``force=True``.
- The first pull that would merge into existing local sessions offers a
  tarball backup first (via the ``confirm`` callback).
"""

from __future__ import annotations

import re
import subprocess
import tarfile
import tempfile
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from claude_hop.config import Config
from claude_hop.remap import PathMapper, remap_tree
from claude_hop.transport import Transport

_PULL_MARKER = ".claude-hop-pulled"

RUNNING_MSG = (
    "Claude Code appears to be running — it flushes sessions to disk on exit, "
    "so the latest work would be missed. Quit it first, or pass --force."
)


class SyncError(Exception):
    """A sync could not proceed."""


def claude_running() -> bool:
    """Best-effort check for a running `claude` CLI process."""
    try:
        out = subprocess.run(
            ["pgrep", "-fl", "claude"], capture_output=True, text=True
        ).stdout
    except FileNotFoundError:
        return False
    return any(
        re.search(r"(^|/)claude(\s|$)", line.split(maxsplit=1)[-1])
        for line in out.splitlines()
    )


@dataclass
class ChangeSummary:
    new: int = 0
    updated: int = 0


@dataclass
class SyncReport:
    changes: list[str]  # rsync --itemize-changes lines
    dry_run: bool
    backup: Path | None = None
    summary: dict[str, ChangeSummary] = field(init=False)

    def __post_init__(self) -> None:
        self.summary = summarize_changes(self.changes)

    @property
    def total_files(self) -> int:
        return sum(s.new + s.updated for s in self.summary.values())


def summarize_changes(changes: list[str]) -> dict[str, ChangeSummary]:
    """Group rsync itemized file changes by top-level project directory."""
    per_project: dict[str, ChangeSummary] = {}
    for line in changes:
        parts = line.split(maxsplit=1)
        if len(parts) != 2:
            continue
        flags, path = parts
        if len(flags) < 2 or flags[1] != "f":  # only files, not dirs/links
            continue
        project = path.split("/", 1)[0]
        summary = per_project.setdefault(project, ChangeSummary())
        if "+" in flags:  # e.g. ">f+++++++++" = file did not exist on target
            summary.new += 1
        else:
            summary.updated += 1
    return per_project


def _noop(_msg: str) -> None:
    pass


def _projects_dir(local_home: Path) -> Path:
    return local_home / ".claude" / "projects"


def push(
    cfg: Config,
    *,
    local_home: str | Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    log: Callable[[str], None] = _noop,
) -> SyncReport:
    """Remap local sessions into a staging dir and merge them onto the remote."""
    local_home = Path(local_home) if local_home else Path.home()
    src = _projects_dir(local_home)
    if not src.is_dir() or not any(src.iterdir()):
        raise SyncError(f"no local sessions at {src}")
    if not force and claude_running():
        raise SyncError(RUNNING_MSG)
    mapper = PathMapper.for_push(local_home, cfg.remote_home, cfg.mappings)
    transport = Transport(cfg.host, cfg.remote_home)
    with tempfile.TemporaryDirectory(prefix="claude-hop-") as tmp:
        staging = Path(tmp) / "projects"
        log("remapping sessions into staging…")
        remap_tree(src, staging, mapper)
        if not dry_run:
            transport.ensure_remote_projects()
        log("syncing to remote…")
        changes = transport.rsync(
            f"{staging}/", transport.remote_projects_arg + "/", dry_run=dry_run
        )
    return SyncReport(changes=changes, dry_run=dry_run)


def pull(
    cfg: Config,
    *,
    local_home: str | Path | None = None,
    dry_run: bool = False,
    force: bool = False,
    confirm: Callable[[str], bool] = lambda _msg: True,
    log: Callable[[str], None] = _noop,
) -> SyncReport:
    """Fetch remote sessions, remap them for this machine, and merge them in."""
    local_home = Path(local_home) if local_home else Path.home()
    dest = _projects_dir(local_home)
    if not force and claude_running():
        raise SyncError(RUNNING_MSG)
    transport = Transport(cfg.host, cfg.remote_home)
    if not transport.remote_projects_exists():
        raise SyncError(f"no sessions on the remote ({transport.remote_projects} is missing)")
    mapper = PathMapper.for_pull(local_home, cfg.remote_home, cfg.mappings)
    backup: Path | None = None
    with tempfile.TemporaryDirectory(prefix="claude-hop-") as tmp:
        raw = Path(tmp) / "raw"
        staging = Path(tmp) / "projects"
        raw.mkdir()
        log("fetching remote sessions…")
        transport.rsync(transport.remote_projects_arg + "/", f"{raw}/")
        log("remapping paths…")
        remap_tree(raw, staging, mapper)

        marker = local_home / ".claude" / _PULL_MARKER
        first_pull = not marker.exists()
        if (
            not dry_run
            and first_pull
            and dest.is_dir()
            and any(dest.iterdir())
            and confirm(f"First pull into existing sessions at {dest} — back them up first?")
        ):
            log("backing up local sessions…")
            backup = _make_backup(dest, local_home)

        if not dry_run:
            dest.mkdir(parents=True, exist_ok=True)
        log("merging into local sessions…")
        changes = transport.rsync(f"{staging}/", f"{dest}/", dry_run=dry_run)
        if not dry_run:
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.touch()
    return SyncReport(changes=changes, dry_run=dry_run, backup=backup)


def _make_backup(projects: Path, local_home: Path) -> Path:
    backups = local_home / ".claude" / "backups"
    backups.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    path = backups / f"claude-hop-projects-{stamp}.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        tar.add(projects, arcname="projects")
    return path
