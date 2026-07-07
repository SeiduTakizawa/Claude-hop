"""Typer CLI. All logic lives in sync/transport/remap/config; this module
only parses arguments, prompts, and renders rich output.

Environment hooks (used by tests and the e2e harness):
- ``CLAUDE_HOP_CONFIG`` — config file path (same as ``--config``)
- ``CLAUDE_HOP_HOME`` — treat this directory as the local home
"""

import os
import subprocess
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from claude_hop import __version__
from claude_hop import config as config_mod
from claude_hop import sync as sync_mod
from claude_hop.banner import get_version, show_banner
from claude_hop.config import Config, ConfigError, Remote, resolve_remote
from claude_hop.remap import PathMapper
from claude_hop.sync import SyncError
from claude_hop.transport import TransportError, local_rsync_available

app = typer.Typer(
    help="Sync Claude Code sessions between two machines over SSH — no cloud.",
    no_args_is_help=True,
)
console = Console()
err_console = Console(stderr=True)
_state: dict = {"config_path": None}

_FAILURES = (ConfigError, SyncError, TransportError, ValueError)


def _version_callback(value: bool) -> None:
    if value:
        print(f"claude-hop {__version__}")
        raise typer.Exit()


@app.callback()
def _root(
    config: Path | None = typer.Option(
        None,
        "--config",
        "-c",
        envvar="CLAUDE_HOP_CONFIG",
        help="Config file (default: ~/.config/claude-hop/config.toml).",
    ),
    version: bool = typer.Option(
        False, "--version", callback=_version_callback, is_eager=True,
        help="Print version and exit.",
    ),
) -> None:
    _state["config_path"] = config


def _config_path() -> Path:
    return _state["config_path"] or config_mod.default_config_path()


def _local_home() -> Path:
    return Path(os.environ.get("CLAUDE_HOP_HOME") or Path.home())


def _fail(exc: Exception) -> typer.Exit:
    console.print(f"[red]✗[/red] {exc}")
    return typer.Exit(1)


def _load_config() -> Config:
    try:
        cfg = config_mod.load(_state["config_path"])
    except _FAILURES as e:
        raise _fail(e) from e
    if cfg.legacy:
        err_console.print(
            "[dim]note: this config uses the old single-remote format — "
            "run claude-hop remotes migrate to upgrade it[/dim]"
        )
    return cfg


def _log(msg: str) -> None:
    console.print(f"[dim]•[/dim] {msg}")


@app.command()
def init() -> None:
    """Interactive setup: SSH host, connection test, remote-home detection."""
    show_banner(console, get_version())
    path = _config_path()
    if path.exists():
        typer.confirm(f"{path} exists — overwrite?", abort=True)

    host = typer.prompt(
        "SSH host of the other machine (alias or hostname; leave empty for a local directory)",
        default="",
        show_default=False,
    ).strip()

    remote_home = ""
    if host:
        with console.status(f"connecting to {host}…"):
            detected, remote_rsync, error = _probe_remote(host)
        if error:
            console.print(f"[red]✗[/red] could not reach {host}: {error}")
            typer.confirm("Continue anyway and enter the remote home manually?", abort=True)
        else:
            console.print(f"[green]✓[/green] SSH connection to {host} works")
            if remote_rsync:
                console.print("[green]✓[/green] rsync found on the remote")
            else:
                console.print("[yellow]⚠[/yellow] rsync not found on the remote — install it")
            if detected:
                console.print(f"[green]✓[/green] detected remote home: {detected}")
        remote_home = detected or typer.prompt("Remote home directory (e.g. /Users/alice)").strip()
    else:
        remote_home = typer.prompt(
            "Local directory that plays the remote home (absolute path)"
        ).strip()

    if not remote_home.startswith("/"):
        raise _fail(ValueError(f"home must be an absolute path, got {remote_home!r}"))

    name = typer.prompt("Name for this remote", default=_suggest_name(host)).strip()
    if not config_mod._NAME_RE.match(name):
        raise _fail(ValueError(f"invalid remote name {name!r} — letters, digits, - and _ only"))

    if local_rsync_available():
        console.print("[green]✓[/green] rsync found locally")
    else:
        console.print("[yellow]⚠[/yellow] rsync not found locally — install it before syncing")

    remote = Remote(name=name, host=host, home=remote_home.rstrip("/"))
    config_mod.save(Config(remotes={name: remote}), path)
    console.print(f"[green]✓[/green] wrote {path}")
    console.print("Run [bold]claude-hop status[/bold] to check how your projects will map.")


def _suggest_name(host: str) -> str:
    """A short default remote name derived from the SSH host."""
    if not host:
        return "local"
    label = host.split("@")[-1].split(".")[0]
    label = "".join(c for c in label if c.isalnum() or c in "-_")
    return label.lower() if label and label[0].isalnum() else "remote"


def _probe_remote(host: str) -> tuple[str, bool, str]:
    """Returns (detected_home, remote_has_rsync, error). error="" on success."""
    try:
        r = subprocess.run(
            ["ssh", host, 'echo "$HOME"; command -v rsync || echo __NO_RSYNC__'],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except subprocess.TimeoutExpired:
        return "", False, "connection timed out"
    except FileNotFoundError:
        return "", False, "ssh not found on this machine"
    if r.returncode != 0:
        return "", False, r.stderr.strip() or f"ssh exited with {r.returncode}"
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    home = lines[0] if lines else ""
    has_rsync = len(lines) > 1 and lines[1] != "__NO_RSYNC__"
    return home, has_rsync, ""


@app.command()
def status() -> None:
    """Show local projects and the remapped name each gets on the remote."""
    cfg = _load_config()
    local_home = _local_home()
    projects = local_home / ".claude" / "projects"
    if not projects.is_dir():
        raise _fail(SyncError(f"no local sessions at {projects}"))
    try:
        target = resolve_remote(cfg, None)
        mapper = PathMapper.for_push(local_home, target.home, cfg.merged_mappings(target.name))
    except _FAILURES as e:
        raise _fail(e) from e
    back = mapper.inverted()

    table = Table(title=f"Local projects → {target.host or target.home}")
    table.add_column("Project", overflow="fold")
    table.add_column("Sessions", justify="right")
    table.add_column("On remote", overflow="fold")
    table.add_column("Notes")
    targets_seen: dict[str, str] = {}
    problems = 0
    for proj in sorted(p for p in projects.iterdir() if p.is_dir()):
        mapped = mapper.remap_dirname(proj.name)
        notes = []
        if mapped == proj.name:
            notes.append("[dim]unchanged[/dim]")
        if back.remap_dirname(mapped) != proj.name:
            notes.append("[yellow]⚠ ambiguous: won't round-trip[/yellow]")
            problems += 1
        if mapped in targets_seen:
            notes.append(f"[red]✗ collides with {targets_seen[mapped]}[/red]")
            problems += 1
        else:
            targets_seen[mapped] = proj.name
        sessions = len(list(proj.glob("*.jsonl")))
        table.add_row(proj.name, str(sessions), mapped, " ".join(notes))
    console.print(table)
    if problems:
        console.print(
            f"[yellow]⚠ {problems} mapping problem(s) — fix them with a \\[mappings] entry "
            f"in {_config_path()}[/yellow]"
        )
    else:
        console.print(r"[dim]Wrong mapping? Add the project under \[mappings] in the config.[/dim]")


def _refuse_if_running(force: bool) -> None:
    if not force and sync_mod.claude_running():
        console.print(f"[red]✗[/red] {sync_mod.RUNNING_MSG}")
        raise typer.Exit(1)


def _summary_block(report: sync_mod.SyncReport) -> None:
    if not report.summary:
        console.print("[green]✓[/green] already in sync — nothing to transfer")
        return
    table = Table()
    table.add_column("Project", overflow="fold")
    table.add_column("New", justify="right")
    table.add_column("Updated", justify="right")
    for project, s in sorted(report.summary.items()):
        table.add_row(project, str(s.new), str(s.updated))
    console.print(table)
    verb = "would transfer" if report.dry_run else "transferred"
    console.print(f"[green]✓[/green] {verb} {report.total_files} file(s)")


def _render_report(report: sync_mod.SyncReport, done_msg: str) -> None:
    if report.dry_run:
        console.print("[cyan]dry run — nothing was written[/cyan]")
    _summary_block(report)
    if report.summary and not report.dry_run:
        console.print(done_msg)
    if report.backup:
        console.print(f"[dim]local sessions backed up to {report.backup}[/dim]")


_PROJECTS_HELP = (
    "Sync only these projects — paths (e.g. '.') or encoded names from status. Default: all."
)


@app.command()
def push(
    projects: list[str] | None = typer.Argument(None, help=_PROJECTS_HELP),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would sync."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Answer yes to prompts."),
    force: bool = typer.Option(False, "--force", help="Sync even if Claude Code is running."),
) -> None:
    """Send local sessions to the remote machine (merge, newer file wins)."""
    cfg = _load_config()
    _refuse_if_running(force)
    try:
        report = sync_mod.push(
            cfg,
            local_home=_local_home(),
            projects=projects or None,
            dry_run=dry_run,
            force=True,
            log=_log,
        )
    except _FAILURES as e:
        raise _fail(e) from e
    _render_report(report, "run [bold]claude --resume[/bold] on the other machine")


@app.command()
def pull(
    projects: list[str] | None = typer.Argument(None, help=_PROJECTS_HELP),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would sync."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Answer yes to prompts."),
    force: bool = typer.Option(False, "--force", help="Sync even if Claude Code is running."),
) -> None:
    """Fetch remote sessions and merge them in (merge, newer file wins)."""
    cfg = _load_config()
    _refuse_if_running(force)
    confirm = (lambda _msg: True) if yes else (lambda msg: typer.confirm(msg, default=True))
    try:
        report = sync_mod.pull(
            cfg,
            local_home=_local_home(),
            projects=projects or None,
            dry_run=dry_run,
            force=True,
            confirm=confirm,
            log=_log,
        )
    except _FAILURES as e:
        raise _fail(e) from e
    _render_report(report, "run [bold]claude --resume[/bold] to pick up where you left off")


@app.command()
def diff(
    projects: list[str] | None = typer.Argument(None, help=_PROJECTS_HELP),
) -> None:
    """Show what would sync in each direction, without writing anything."""
    cfg = _load_config()
    home = _local_home()
    selection = projects or None
    try:
        target = resolve_remote(cfg, None)
    except _FAILURES as e:
        raise _fail(e) from e
    where = target.host or target.home
    console.print(f"[bold]Outgoing[/bold] (push → {where})")
    try:
        _summary_block(
            sync_mod.push(cfg, local_home=home, projects=selection, dry_run=True, force=True)
        )
    except _FAILURES as e:
        console.print(f"[yellow]~[/yellow] {e}")
    console.print(f"\n[bold]Incoming[/bold] (pull ← {where})")
    try:
        _summary_block(
            sync_mod.pull(cfg, local_home=home, projects=selection, dry_run=True, force=True)
        )
    except _FAILURES as e:
        console.print(f"[yellow]~[/yellow] {e}")


@app.command()
def doctor() -> None:
    """Check SSH, rsync, config, and local state for problems."""
    from claude_hop import doctor as doctor_mod

    checks = doctor_mod.run_checks(_state["config_path"], _local_home())
    icons = {
        doctor_mod.OK: "[green]✓[/green]",
        doctor_mod.WARN: "[yellow]⚠[/yellow]",
        doctor_mod.FAIL: "[red]✗[/red]",
    }
    table = Table(show_header=False, box=None, pad_edge=False)
    table.add_column()
    table.add_column(style="bold")
    table.add_column(overflow="fold")
    for check in checks:
        table.add_row(icons[check.status], check.name, check.detail)
    console.print(table)
    failed = sum(1 for c in checks if c.status == doctor_mod.FAIL)
    if failed:
        console.print(f"[red]✗ {failed} check(s) failed[/red]")
        raise typer.Exit(1)
    console.print("[green]✓ ready to sync[/green]")


@app.command()
def upgrade() -> None:
    """Upgrade claude-hop to the latest release."""
    from claude_hop import upgrade as upgrade_mod

    current = get_version()
    with console.status("checking PyPI for the latest release…"):
        latest = upgrade_mod.latest_version()
    if latest is None:
        console.print("[yellow]⚠[/yellow] could not reach PyPI — attempting the upgrade anyway")
    elif not upgrade_mod.is_newer(latest, current):
        console.print(f"[green]✓[/green] already up to date (v{current})")
        return
    else:
        console.print(f"upgrading v{current} → v{latest}")

    cmd = upgrade_mod.detect_upgrade_command()
    if cmd is None:
        if upgrade_mod.dev_checkout_root() is not None:
            raise _fail(SyncError("this is a development checkout — upgrade with git pull"))
        raise _fail(
            SyncError(
                "couldn't tell how claude-hop was installed — upgrade with your installer: "
                "uv tool upgrade claude-hop | pipx upgrade claude-hop | "
                "pip install -U claude-hop"
            )
        )
    console.print(f"[dim]$ {' '.join(cmd)}[/dim]")
    result = subprocess.run(cmd)
    if result.returncode != 0:
        raise _fail(SyncError(f"upgrade command failed (exit {result.returncode})"))
    console.print("[green]✓[/green] done — [bold]claude-hop --version[/bold] to confirm")


def main() -> None:
    app()
