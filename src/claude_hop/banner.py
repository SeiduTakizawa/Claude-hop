"""Startup banner for `claude-hop init`.

The logo is a static figlet (ansi_shadow) rendering, 78 columns wide —
stored verbatim, styled with rich only. No art on non-terminals or in
terminals narrower than the logo: piped logs and wrapped box-drawing are
worse than no banner.
"""

from __future__ import annotations

from importlib import metadata

from rich.console import Console

LOGO = r"""
 ██████╗██╗      █████╗ ██╗   ██╗██████╗ ███████╗    ██╗  ██╗ ██████╗ ██████╗
██╔════╝██║     ██╔══██╗██║   ██║██╔══██╗██╔════╝    ██║  ██║██╔═══██╗██╔══██╗
██║     ██║     ███████║██║   ██║██║  ██║█████╗█████╗███████║██║   ██║██████╔╝
██║     ██║     ██╔══██║██║   ██║██║  ██║██╔══╝╚════╝██╔══██║██║   ██║██╔═══╝
╚██████╗███████╗██║  ██║╚██████╔╝██████╔╝███████╗    ██║  ██║╚██████╔╝██║
 ╚═════╝╚══════╝╚═╝  ╚═╝ ╚═════╝ ╚═════╝ ╚══════╝    ╚═╝  ╚═╝ ╚═════╝ ╚═╝
""".strip("\n")

TAGLINE = "Sync your Claude Code sessions between machines — no cloud, just SSH."


def get_version() -> str:
    try:
        return metadata.version("claude-hop")
    except metadata.PackageNotFoundError:
        return "dev"


def show_banner(console: Console, version: str) -> None:
    if not console.is_terminal or console.width < 80:
        console.print(f"claude-hop v{version}", highlight=False)
        return
    console.print(LOGO, style="bold #ff8800", highlight=False)
    console.print(f"[bold]Welcome to claude-hop![/bold] [dim]v{version}[/dim]", highlight=False)
    console.print(f"[dim]{TAGLINE}[/dim]")
    console.print()
