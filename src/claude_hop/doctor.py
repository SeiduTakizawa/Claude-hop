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
            _check_remote(checks, remote, suffix)
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
        try:
            r = transport.run_ssh("echo __claude_hop_ok__", timeout=15)
        except TransportError as e:
            checks.append(Check("ssh" + suffix, FAIL, str(e)))
            return
        if r.returncode != 0 or "__claude_hop_ok__" not in r.stdout:
            detail = r.stderr.strip().splitlines()[-1] if r.stderr.strip() else "unreachable"
            checks.append(Check("ssh" + suffix, FAIL, f"cannot reach {remote.host!r}: {detail}"))
            return
        checks.append(Check("ssh" + suffix, OK, f"{remote.host!r} reachable"))

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
