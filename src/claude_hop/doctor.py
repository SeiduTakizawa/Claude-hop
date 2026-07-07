"""Environment checks for `claude-hop doctor`.

Each check returns ok/warn/fail with a human detail line; the CLI renders
them and exits non-zero if anything failed. Checks degrade gracefully: a
broken config fails its own check and the remote checks are skipped.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from claude_hop import config as config_mod
from claude_hop import sync
from claude_hop.config import Config, ConfigError, Remote, resolve_remote
from claude_hop.remap import PathMapper
from claude_hop.transport import Transport, TransportError

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
    checks: list[Check] = []
    resolved = _check_config(checks, config_path, local_home, remote_name)
    _check_local_sessions(checks, local_home)
    _check_local_rsync(checks)
    if resolved is not None:
        _, remote = resolved
        _check_remote(checks, remote)
    _check_claude_running(checks)
    _check_stale_temp_dirs(checks)
    return checks


def _check_config(
    checks: list[Check],
    config_path: Path | None,
    local_home: Path,
    remote_name: str | None,
) -> tuple[Config, Remote] | None:
    try:
        cfg = config_mod.load(config_path)
    except ConfigError as e:
        checks.append(Check("config", FAIL, str(e)))
        return None
    try:
        remote = resolve_remote(cfg, remote_name)
    except ConfigError as e:
        checks.append(Check("config", FAIL, str(e)))
        return None
    where = f"SSH host {remote.host!r}" if remote.host else f"local path {remote.home!r}"
    checks.append(Check("config", OK, f"valid — remote {remote.name!r} is {where}"))
    mappings = cfg.merged_mappings(remote.name)
    try:
        PathMapper.for_push(local_home, remote.home, mappings)
        checks.append(Check("mappings", OK, f"{len(mappings)} explicit mapping(s)"))
    except ValueError as e:
        checks.append(Check("mappings", FAIL, str(e)))
        return None
    return cfg, remote


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


def _check_remote(checks: list[Check], remote: Remote) -> None:
    transport = Transport(remote.host, remote.home)
    if not remote.host:
        checks.append(Check("ssh", OK, "local-path mode — no SSH involved"))
    else:
        try:
            r = transport.run_ssh("echo __claude_hop_ok__", timeout=15)
        except TransportError as e:
            checks.append(Check("ssh", FAIL, str(e)))
            return
        if r.returncode != 0 or "__claude_hop_ok__" not in r.stdout:
            detail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "unreachable"
            checks.append(Check("ssh", FAIL, f"cannot reach {remote.host!r}: {detail}"))
            return
        checks.append(Check("ssh", OK, f"{remote.host!r} reachable"))

        rv = transport.run_ssh("rsync --version 2>/dev/null | head -n1")
        if rv.returncode != 0 or not rv.stdout.strip():
            checks.append(
                Check("rsync (remote)", FAIL, f"not found on {remote.host!r} — install it")
            )
        else:
            checks.append(Check("rsync (remote)", OK, rv.stdout.strip()))

        hv = transport.run_ssh(f'test -d {shlex.quote(remote.home)}')
        if hv.returncode != 0:
            checks.append(
                Check("remote home", FAIL, f"{remote.home} does not exist on {remote.host!r}")
            )
            return
        checks.append(Check("remote home", OK, remote.home))

    if transport.remote_projects_exists():
        checks.append(Check("remote sessions", OK, transport.remote_projects))
    else:
        checks.append(
            Check(
                "remote sessions",
                WARN,
                f"{transport.remote_projects} does not exist yet — created on first push",
            )
        )


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
