"""Thin command layer over rsync and ssh.

``host = ""`` selects local-path mode: the "remote" home is a directory on
this machine, and rsync runs purely locally. Integration tests use this;
it also works for syncing to a mounted disk.

Safety: rsync always runs with ``-u`` (newer file wins) and never with
``--delete`` — a sync can only add or update, never remove.
"""

from __future__ import annotations

import shlex
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class TransportError(Exception):
    """rsync/ssh invocation failed or is impossible."""


# probe verdicts
PROBE_OK = "reachable"
PROBE_NO_AUTH = "no-auth"
PROBE_TIMEOUT = "timeout"
PROBE_DNS = "dns"
PROBE_UNREACHABLE = "unreachable"

# BatchMode so a probe can never sit on an invisible password prompt;
# accept-new so first contact reaches the auth layer instead of dying at
# host-key verification.
BATCH_SSH_OPTS = [
    "-o",
    "BatchMode=yes",
    "-o",
    "StrictHostKeyChecking=accept-new",
]


@dataclass(frozen=True)
class ProbeResult:
    verdict: str
    detail: str


def probe_ssh(host: str, *, timeout: int = 8) -> ProbeResult:
    """Reach the host over SSH without ever prompting, and say what
    actually happened — auth failure is not a timeout."""
    cmd = ["ssh", *BATCH_SSH_OPTS, "-o", f"ConnectTimeout={timeout}", host, "true"]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout + 15)
    except subprocess.TimeoutExpired:
        return ProbeResult(PROBE_TIMEOUT, f"connection to {host!r} timed out")
    except FileNotFoundError:
        return ProbeResult(PROBE_UNREACHABLE, "ssh not found on this machine")
    if r.returncode == 0:
        return ProbeResult(PROBE_OK, f"{host!r} reachable")
    return classify_ssh_error(host, r.stderr)


def classify_ssh_error(host: str, stderr: str) -> ProbeResult:
    """Map ssh's stderr to a verdict with the fix that actually applies."""
    lowered = stderr.lower()
    if "permission denied" in lowered or "too many authentication failures" in lowered:
        return ProbeResult(
            PROBE_NO_AUTH,
            f"host reachable, but key-based auth isn't set up — run: ssh-copy-id {host}",
        )
    if "timed out" in lowered:
        return ProbeResult(PROBE_TIMEOUT, f"connection to {host!r} timed out")
    if "could not resolve hostname" in lowered or "name or service not known" in lowered:
        return ProbeResult(PROBE_DNS, f"could not resolve host {host!r}")
    last = stderr.strip().splitlines()[-1] if stderr.strip() else f"cannot reach {host!r}"
    return ProbeResult(PROBE_UNREACHABLE, last)


@dataclass(frozen=True)
class Transport:
    host: str  # SSH host or alias; "" = local-path mode
    remote_home: str

    @property
    def remote_projects(self) -> str:
        return f"{self.remote_home}/.claude/projects"

    @property
    def remote_projects_arg(self) -> str:
        """The remote projects dir as an rsync source/target argument."""
        return self._remote_arg(self.remote_projects)

    def _remote_arg(self, path: str) -> str:
        if not self.host:
            return path
        # the remote path is expanded by the remote shell — quote it
        return f"{self.host}:{shlex.quote(path)}"

    def run_ssh(self, command: str, *, timeout: int = 30) -> subprocess.CompletedProcess[str]:
        if not self.host:
            raise TransportError("not an SSH remote (local-path mode)")
        try:
            return subprocess.run(
                ["ssh", self.host, command],
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired as e:
            raise TransportError(f"ssh to {self.host!r} timed out after {timeout}s") from e
        except FileNotFoundError as e:
            raise TransportError("ssh not found on this machine") from e

    def remote_claude_path(self, name: str) -> str:
        """Absolute remote path of ~/.claude/<name>."""
        return f"{self.remote_home}/.claude/{name}"

    def remote_arg(self, path: str) -> str:
        return self._remote_arg(path)

    def remote_exists(self, path: str) -> bool:
        if not self.host:
            return Path(path).exists()
        return self.run_ssh(f"test -e {shlex.quote(path)}").returncode == 0

    def remote_projects_exists(self) -> bool:
        if not self.host:
            return Path(self.remote_projects).is_dir()
        return self.run_ssh(f"test -d {shlex.quote(self.remote_projects)}").returncode == 0

    def ensure_remote_projects(self) -> None:
        if not self.host:
            try:
                Path(self.remote_projects).mkdir(parents=True, exist_ok=True)
            except OSError as e:
                raise TransportError(f"could not create {self.remote_projects}: {e}") from e
            return
        r = self.run_ssh(f"mkdir -p {shlex.quote(self.remote_projects)}")
        if r.returncode != 0:
            raise TransportError(
                f"could not create {self.remote_projects} on {self.host}: {r.stderr.strip()}"
            )

    def rsync(
        self, src: str, dst: str, *, dry_run: bool = False, newer_wins: bool = True
    ) -> list[str]:
        """Merge-copy src into dst; returns rsync's itemized change lines.

        ``-u`` keeps whichever file is newer; nothing is ever deleted.
        ``newer_wins=False`` drops ``-u`` — used only for the --verbose
        dry-run that surfaces files a real merge skipped.
        """
        cmd = ["rsync", "-a", "--itemize-changes", "--exclude", "*.tmp"]
        if newer_wins:
            cmd.append("-u")
        if dry_run:
            cmd.append("--dry-run")
        cmd += [src, dst]
        try:
            r = subprocess.run(cmd, capture_output=True, text=True)
        except FileNotFoundError as e:
            raise TransportError("rsync not found on this machine — install it first") from e
        if r.returncode != 0:
            detail = r.stderr.strip() or r.stdout.strip()
            raise TransportError(f"rsync failed (exit {r.returncode}): {detail}")
        return [line for line in r.stdout.splitlines() if line.strip()]


def local_rsync_available() -> bool:
    return shutil.which("rsync") is not None
