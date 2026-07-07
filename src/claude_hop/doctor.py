"""Environment checks for `claude-hop doctor`.

Each check returns ok/warn/fail with a human detail line; the CLI renders
them and exits non-zero if anything failed. Checks degrade gracefully: a
broken config fails its own check and the remote checks are skipped.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from claude_hop import config as config_mod
from claude_hop import projects as projects_mod
from claude_hop import sync
from claude_hop.config import Config, ConfigError, Remote, resolve_remote
from claude_hop.remap import PathMapper
from claude_hop.transport import PROBE_OK, Transport, probe_ssh

OK = "ok"
WARN = "warn"
FAIL = "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str


def run_checks(
    config_path: Path | None, local_home: Path, remote_name: str | None = None
) -> list[Check]:
    """Environment checks. With no ``remote_name``, every configured remote
    is checked; check names get a " (name)" suffix when there are several."""
    checks: list[Check] = []
    cfg = _check_config(checks, config_path)
    _check_local_sessions(checks, local_home)
    _check_local_rsync(checks)
    if cfg is not None:
        targets: list[Remote] = []
        if remote_name is not None:
            # same resolution (and same unknown-name error) as push/pull/diff
            targets = [resolve_remote(cfg, remote_name)]
        elif not cfg.remotes:
            checks.append(Check("remotes", WARN, "no remotes configured — run: claude-hop init"))
        else:
            targets = list(cfg.remotes.values())
        many = len(targets) > 1
        for remote in targets:
            suffix = f" ({remote.name})" if many else ""
            _check_mappings(checks, cfg, remote, local_home, suffix)
            _check_project_coverage(checks, cfg, remote, local_home, suffix)
            _check_remote(checks, remote, suffix)
    _check_git_state(checks, local_home)
    _check_other_stores(checks, local_home)
    _check_claude_running(checks)
    _check_stale_temp_dirs(checks)
    return checks


def _check_config(checks: list[Check], config_path: Path | None) -> Config | None:
    try:
        cfg = config_mod.load(config_path)
    except ConfigError as e:
        checks.append(Check("config", FAIL, str(e)))
        return None
    if cfg.remotes:
        detail = f"valid — {len(cfg.remotes)} remote(s): " + ", ".join(cfg.remotes)
    else:
        detail = "valid — no remotes configured yet"
    checks.append(Check("config", OK, detail))
    return cfg


def _check_mappings(
    checks: list[Check], cfg: Config, remote: Remote, local_home: Path, suffix: str
) -> None:
    mappings = cfg.merged_mappings(remote.name)
    try:
        PathMapper.for_push(local_home, remote.home, mappings)
        checks.append(Check("mappings" + suffix, OK, f"{len(mappings)} explicit mapping(s)"))
    except ValueError as e:
        checks.append(Check("mappings" + suffix, FAIL, str(e)))


def _check_local_sessions(checks: list[Check], local_home: Path) -> None:
    projects = local_home / ".claude" / "projects"
    if not projects.is_dir():
        checks.append(
            Check("local sessions", WARN, f"{projects} does not exist — nothing to push yet")
        )
        return
    dirs = [p for p in projects.iterdir() if p.is_dir()]
    sessions = sum(len(list(p.glob("*.jsonl"))) for p in dirs)
    checks.append(
        Check("local sessions", OK, f"{len(dirs)} project(s), {sessions} session file(s)")
    )


def _check_local_rsync(checks: list[Check]) -> None:
    if not shutil.which("rsync"):
        checks.append(Check("rsync (local)", FAIL, "not found — install it"))
        return
    checks.append(Check("rsync (local)", OK, _rsync_version(["rsync", "--version"])))


def _check_remote(checks: list[Check], remote: Remote, suffix: str = "") -> None:
    transport = Transport(remote.host, remote.home)
    if not remote.host:
        checks.append(Check("ssh" + suffix, OK, "local-path mode — no SSH involved"))
    else:
        result = probe_ssh(remote.host, timeout=10)
        if result.verdict != PROBE_OK:
            checks.append(Check("ssh" + suffix, FAIL, result.detail))
            return
        checks.append(Check("ssh" + suffix, OK, result.detail))

        rv = transport.run_ssh("rsync --version 2>/dev/null | head -n1")
        if rv.returncode != 0 or not rv.stdout.strip():
            checks.append(
                Check(f"rsync (remote){suffix}", FAIL, f"not found on {remote.host!r} — install it")
            )
        else:
            checks.append(Check(f"rsync (remote){suffix}", OK, rv.stdout.strip()))

        hv = transport.run_ssh(f'test -d {shlex.quote(remote.home)}')
        if hv.returncode != 0:
            checks.append(
                Check(
                    "remote home" + suffix,
                    FAIL,
                    f"{remote.home} does not exist on {remote.host!r}",
                )
            )
            return
        checks.append(Check("remote home" + suffix, OK, remote.home))
        _check_clock_skew(checks, transport, suffix)

    if transport.remote_projects_exists():
        checks.append(Check("remote sessions" + suffix, OK, transport.remote_projects))
    else:
        checks.append(
            Check(
                "remote sessions" + suffix,
                WARN,
                f"{transport.remote_projects} does not exist yet — created on first push",
            )
        )


def _check_project_coverage(
    checks: list[Check], cfg: Config, remote: Remote, local_home: Path, suffix: str
) -> None:
    """Projects outside the home that no mapping covers would land with
    unresumable paths on this remote."""
    try:
        mapper = PathMapper.for_push(local_home, remote.home, cfg.merged_mappings(remote.name))
    except ValueError:
        return  # the mappings check already failed
    report = projects_mod.find_outside_home(
        local_home / ".claude" / "projects", local_home, mapper
    )
    if report:
        shown = ", ".join(report.names[:3]) + ("…" if len(report.names) > 3 else "")
        checks.append(
            Check(
                "project coverage" + suffix,
                WARN,
                f"{len(report.names)} project(s) outside home, not covered by any "
                f"mapping: {shown} — push/pull will refuse them",
            )
        )
    else:
        checks.append(
            Check("project coverage" + suffix, OK, "home remap or mappings cover every project")
        )


def _check_git_state(checks: list[Check], local_home: Path) -> None:
    warnings = projects_mod.git_warnings(local_home / ".claude" / "projects")
    if not warnings:
        checks.append(Check("git state", OK, "no unpushed work detected in project repos"))
        return
    parts = []
    for _name, state in warnings[:4]:
        flags = []
        if state.dirty:
            flags.append("dirty")
        if state.ahead is None:
            flags.append("no upstream")
        elif state.ahead:
            flags.append(f"{state.ahead} unpushed")
        parts.append(f"{os.path.basename(state.path)} ({', '.join(flags)})")
    more = "…" if len(warnings) > 4 else ""
    checks.append(
        Check(
            "git state",
            WARN,
            f"{len(warnings)} repo(s) with work that hasn't traveled via git: "
            f"{', '.join(parts)}{more} — sessions will arrive before the code does",
        )
    )


def _check_other_stores(
    checks: list[Check],
    local_home: Path,
    roots: tuple[str, ...] = ("/home", "/root"),
    euid: int | None = None,
) -> None:
    """Other users' ~/.claude stores on this machine — claude-hop only syncs
    the current user's. The classic trap is running as root on WSL."""
    euid = os.geteuid() if euid is None else euid
    active = (Path(local_home) / ".claude" / "projects").resolve()
    found: list[Path] = []
    for root in roots:
        root_path = Path(root)
        homes = [root_path] if root == "/root" else None
        try:
            homes = homes if homes is not None else sorted(
                d for d in root_path.iterdir() if d.is_dir()
            )
        except OSError:
            continue
        for home in homes:
            store = home / ".claude" / "projects"
            try:
                if not store.is_dir() or store.resolve() == active:
                    continue
                if any(p.is_dir() for p in store.iterdir()):
                    found.append(home / ".claude")
            except OSError:
                continue  # unreadable: skip silently
    if not found:
        return
    names = ", ".join(str(f) for f in found)
    plural = "stores exist" if len(found) > 1 else "store exists"
    if euid == 0 and any(not str(f).startswith("/root") for f in found):
        checks.append(
            Check(
                "session stores",
                WARN,
                f"running as root: syncing /root/.claude, but another Claude Code "
                f"session {plural} at {names} — claude-hop only syncs the store "
                "of the current user",
            )
        )
    else:
        checks.append(
            Check(
                "session stores",
                OK,
                f"another Claude Code session {plural} at {names} — claude-hop "
                "only syncs the store of the current user",
            )
        )


_MAX_SKEW_SECONDS = 30


def _check_clock_skew(checks: list[Check], transport: Transport, suffix: str = "") -> None:
    """Merge is newer-file-wins, so clocks are load-bearing: skewed clocks
    can make a real update look older than the stale copy."""
    r = transport.run_ssh("date +%s", timeout=10)
    if r.returncode != 0:
        return  # reachability problems are already reported by the ssh check
    try:
        remote_epoch = int(r.stdout.strip())
    except ValueError:
        return
    skew = abs(int(time.time()) - remote_epoch)
    if skew > _MAX_SKEW_SECONDS:
        checks.append(
            Check(
                "clock skew" + suffix,
                WARN,
                f"clock skew of {skew}s with {transport.host!r} — "
                "newer-wins merging may skip updates",
            )
        )
    else:
        checks.append(Check("clock skew" + suffix, OK, f"{skew}s"))


def _check_claude_running(checks: list[Check]) -> None:
    if sync.claude_running():
        checks.append(
            Check(
                "claude code",
                WARN,
                "appears to be running — sessions flush to disk only on exit",
            )
        )
    else:
        checks.append(Check("claude code", OK, "not running"))


def _check_stale_temp_dirs(checks: list[Check]) -> None:
    stale = sorted(Path(tempfile.gettempdir()).glob("claude-hop-*"))
    if stale:
        checks.append(
            Check(
                "temp dirs",
                WARN,
                f"{len(stale)} stale staging dir(s) from interrupted runs, "
                f"e.g. {stale[0]} — safe to delete",
            )
        )
    else:
        checks.append(Check("temp dirs", OK, "no stale staging dirs"))


def _rsync_version(cmd: list[str]) -> str:
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.TimeoutExpired):
        return "version unknown"
    return out.splitlines()[0].strip() if out.strip() else "version unknown"
