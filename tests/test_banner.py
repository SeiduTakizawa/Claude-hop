import io

from rich.console import Console

from claude_hop.banner import LOGO, show_banner


def render(**console_kwargs) -> str:
    buf = io.StringIO()
    console = Console(file=buf, **console_kwargs)
    show_banner(console, "1.2.3")
    return buf.getvalue()


def test_non_tty_gets_plain_header_only():
    out = render()
    assert out == "claude-hop v1.2.3\n"
    assert "█" not in out


def test_narrow_terminal_gets_plain_header():
    out = render(width=60, force_terminal=True)
    assert "claude-hop v1.2.3" in out
    assert "█" not in out


def test_wide_terminal_gets_logo():
    out = render(width=100, force_terminal=True)
    assert "██████╗██╗" in out
    assert "v1.2.3" in out
    assert "Welcome to claude-hop!" in out


def test_logo_is_intact():
    # exactly six lines, and no accidental reflow/strip of the art
    assert len(LOGO.splitlines()) == 6
    assert LOGO.splitlines()[0].startswith(" ██████╗██╗")
