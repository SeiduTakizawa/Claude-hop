"""Self-upgrade: figure out how claude-hop was installed and upgrade it.

Supported installers, detected from ``sys.prefix``: uv tool, pipx, plain
pip. A development checkout (running from a git repo) is refused — that's
what ``git pull`` is for.
"""

from __future__ import annotations

import importlib.util
import json
import shutil
import sys
import urllib.request
from pathlib import Path

PYPI_URL = "https://pypi.org/pypi/claude-hop/json"


def latest_version(timeout: float = 10.0) -> str | None:
    """The newest release on PyPI, or None if it can't be reached."""
    try:
        with urllib.request.urlopen(PYPI_URL, timeout=timeout) as r:
            return json.load(r)["info"]["version"]
    except Exception:
        return None


def is_newer(candidate: str, current: str) -> bool:
    """True if candidate is a newer release than current. Falls back to
    plain inequality when either side isn't dotted integers."""
    try:
        a = tuple(int(part) for part in candidate.split("."))
        b = tuple(int(part) for part in current.split("."))
    except ValueError:
        return candidate != current
    return a > b


def dev_checkout_root() -> Path | None:
    """The git repo root when running from a development checkout."""
    root = Path(__file__).resolve().parents[2]
    return root if (root / ".git").exists() else None


def detect_upgrade_command() -> list[str] | None:
    """The command that upgrades this installation, or None if unknown."""
    if dev_checkout_root() is not None:
        return None
    prefix = str(Path(sys.prefix))
    if "/uv/tools/" in prefix and shutil.which("uv"):
        # --reinstall implies --refresh: uv's index cache can lag right
        # after a release, making a plain `uv tool upgrade` report "Nothing
        # to upgrade" for a version PyPI already serves. We only run this
        # after confirming a newer release exists, so it never reinstalls
        # needlessly.
        return ["uv", "tool", "upgrade", "--reinstall", "claude-hop"]
    if "/pipx/" in prefix and shutil.which("pipx"):
        return ["pipx", "upgrade", "claude-hop"]
    if importlib.util.find_spec("pip") is not None:
        return [sys.executable, "-m", "pip", "install", "--upgrade", "claude-hop"]
    return None
