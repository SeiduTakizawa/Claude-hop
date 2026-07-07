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

    def rsync(self, src: str, dst: str, *, dry_run: bool = False) -> list[str]:
        """Merge-copy src into dst; returns rsync's itemized change lines.

        ``-u`` keeps whichever file is newer; nothing is ever deleted.
        """
        cmd = ["rsync", "-au", "--itemize-changes", "--exclude", "*.tmp"]
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
