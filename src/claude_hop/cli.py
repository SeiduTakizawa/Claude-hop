"""Command-line interface. The Typer app lands in milestone 2; this stub
keeps the ``claude-hop`` console entry point importable in the meantime."""

from __future__ import annotations

import sys

from claude_hop import __version__


def main() -> None:
    print(f"claude-hop {__version__} — CLI commands arrive in milestone 2", file=sys.stderr)
    raise SystemExit(1)
