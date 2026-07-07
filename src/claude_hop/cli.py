"""Typer CLI. All logic lives in sync/transport/remap/config; this module
only parses arguments, prompts, and renders rich output.

Environment hooks (used by tests and the e2e harness):
- ``CLAUDE_HOP_CONFIG`` — config file path (same as ``--config``)
- ``CLAUDE_HOP_HOME`` — treat this directory as the local home
"""

import difflib
import os
import subprocess
from collections.abc import Callable
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
from claude_hop.transport import (
    BATCH_SSH_OPTS,
    PROBE_NO_AUTH,
    PROBE_OK,
    TransportError,
    classify_ssh_error,
    local_rsync_available,
    probe_ssh,
)

app = typer.Typer(
    help="Sync Claude Code sessions between machines over SSH — no cloud.",
    no_args_is_help=True,
)
remotes_app = typer.Typer(help="See and manage the remotes this machine syncs with.")
app.add_typer(remotes_app, name="remotes")
console = Console()
# stderr hints/notices must never line-wrap: piped logs and greps rely on
# the phrase staying on one line
err_console = Console(stderr=True, soft_wrap=True)
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
        _init_existing(path)
        return
    remote = _wizard_remote(name=None)
    config_mod.save(Config(remotes={remote.name: remote}), path)
    console.print(f"[green]✓[/green] wrote {path}")
    console.print("Run [bold]claude-hop status[/bold] to check how your projects will map.")


def _init_existing(path: Path) -> None:
    """init with an existing config: offer to add a remote, never overwrite."""
    try:
        cfg = config_mod.load(path)
    except ConfigError:
        typer.confirm(f"{path} exists but can't be parsed — overwrite it?", abort=True)
        remote = _wizard_remote(name=None)
        config_mod.save(Config(remotes={remote.name: remote}), path)
        console.print(f"[green]✓[/green] wrote {path}")
        return
    existing = ", ".join(cfg.remotes) or "none"
    console.print(f"Config already exists (remotes: {existing}).")
    if cfg.legacy:
        console.print("It uses the old single-remote format.")
        if not typer.confirm("Migrate it to the multi-remote format now?", default=True):
            console.print("nothing changed")
            return
        try:
            backup = config_mod.migrate(path)
        except _FAILURES as e:
            raise _fail(e) from e
        console.print(f"[green]✓[/green] migrated — original kept at {backup}")
        cfg = config_mod.load(path)
    if not typer.confirm("Add a new remote to it?", default=True):
        console.print("nothing changed")
        return
    name = typer.prompt("Name for the new remote").strip()
    _add_remote_to(cfg, name, path)


def _require_new_format(cfg: Config) -> None:
    if cfg.legacy:
        raise _fail(
            ConfigError(
                "this config uses the legacy single-remote format — "
                "run claude-hop remotes migrate first"
            )
        )


def _add_remote_to(cfg: Config, name: str, path: Path) -> None:
    if not config_mod._NAME_RE.match(name):
        raise _fail(ValueError(f"invalid remote name {name!r} — letters, digits, - and _ only"))
    if name in cfg.remotes:
        raise _fail(ConfigError(f"remote {name!r} already exists"))
    cfg.remotes[name] = _wizard_remote(name)
    config_mod.save(cfg, path)
    console.print(f"[green]✓[/green] added remote {name!r} ({len(cfg.remotes)} configured)")


def _wizard_remote(name: str | None) -> Remote:
    """The interactive host/home wizard; prompts for a name when not given."""
    host = typer.prompt(
        "SSH host of the other machine (alias or hostname; leave empty for a local directory)",
        default="",
        show_default=False,
    ).strip()

    detected = ""
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

    if name is None:
        name = typer.prompt("Name for this remote", default=_suggest_name(host)).strip()
        if not config_mod._NAME_RE.match(name):
            raise _fail(ValueError(f"invalid remote name {name!r} — letters, digits, - and _ only"))

    if local_rsync_available():
        console.print("[green]✓[/green] rsync found locally")
    else:
        console.print("[yellow]⚠[/yellow] rsync not found locally — install it before syncing")

    return Remote(name=name, host=host, home=remote_home.rstrip("/"))


def _suggest_name(host: str) -> str:
    """A short default remote name derived from the SSH host."""
    if not host:
        return "local"
    label = host.split("@")[-1].split(".")[0]
    label = "".join(c for c in label if c.isalnum() or c in "-_")
    return label.lower() if label and label[0].isalnum() else "remote"


def _probe_remote(host: str) -> tuple[str, bool, str]:
    """Returns (detected_home, remote_has_rsync, error). error="" on success.

    BatchMode: the wizard's probe must never hang on an invisible password
    prompt — an auth failure is reported as exactly that, with the fix."""
    try:
        r = subprocess.run(
            [
                "ssh",
                *BATCH_SSH_OPTS,
                "-o",
                "ConnectTimeout=10",
                host,
                'echo "$HOME"; command -v rsync || echo __NO_RSYNC__',
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return "", False, "connection timed out"
    except FileNotFoundError:
        return "", False, "ssh not found on this machine"
    if r.returncode != 0:
        return "", False, classify_ssh_error(host, r.stderr).detail
    lines = [line.strip() for line in r.stdout.splitlines() if line.strip()]
    home = lines[0] if lines else ""
    has_rsync = len(lines) > 1 and lines[1] != "__NO_RSYNC__"
    return home, has_rsync, ""


@app.command()
def status(
    remote: str | None = typer.Argument(
        None, help="Remote to compare against (optional when only one is configured)."
    ),
) -> None:
    """Show local projects and the remapped name each gets on the remote."""
    cfg = _load_config()
    local_home = _local_home()
    projects = local_home / ".claude" / "projects"
    if not projects.is_dir():
        raise _fail(SyncError(f"no local sessions at {projects}"))
    try:
        target = resolve_remote(cfg, remote)
        mapper = PathMapper.for_push(local_home, target.home, cfg.merged_mappings(target.name))
    except _FAILURES as e:
        raise _fail(e) from e
    back = mapper.inverted()

    table = Table(title=f"Local projects → {target.name} ({target.host or target.home})")
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


def _split_remote_args(cfg: Config, args: list[str]) -> tuple[str | None, list[str]]:
    """The first positional is a remote iff it exactly matches a configured
    remote name; everything else is a project selector."""
    if not args:
        return None, []
    first = args[0]
    if first in cfg.remotes:
        if (Path.cwd() / first).is_dir():
            err_console.print(
                f"[dim]note: {first!r} was treated as a remote name — "
                f"use ./{first} to select the project of that name[/dim]"
            )
        return first, args[1:]
    _guard_remote_typos(cfg, args)
    return None, args


def _guard_remote_typos(cfg: Config, selectors: list[str]) -> None:
    """A bare word that names no directory and is close to a remote name is
    almost certainly a typo — fail loudly instead of syncing nothing."""
    for sel in selectors:
        if sel.startswith("-") or "/" in sel or sel in (".", ".."):
            continue
        if (Path.cwd() / sel).exists():
            continue
        close = difflib.get_close_matches(sel, list(cfg.remotes), n=1)
        if close:
            raise _fail(
                SyncError(f"no project matching {sel!r} — did you mean remote {close[0]!r}?")
            )


def _prefixed_log(name: str) -> Callable[[str], None]:
    return lambda msg: console.print(f"[dim]• ({name})[/dim] {msg}")


def _run_all(
    cfg: Config, verb: str, operation: Callable[[str], "sync_mod.SyncReport"]
) -> None:
    """Run a sync against every remote, keep going on failure, summarize."""
    if not cfg.remotes:
        raise _fail(ConfigError("no remotes configured — run: claude-hop init"))
    failures = 0
    table = Table(title=f"{verb} — all remotes")
    table.add_column("Remote")
    table.add_column("Result", overflow="fold")
    table.add_column("Files", justify="right")
    for name in cfg.remotes:
        console.print(f"[bold]— {name}[/bold]")
        try:
            report = operation(name)
        except _FAILURES as e:
            failures += 1
            console.print(f"[red]✗[/red] {e}")
            table.add_row(name, f"[red]✗ {e}[/red]", "—")
            continue
        _summary_block(report)
        result = "[cyan]dry run[/cyan]" if report.dry_run else "[green]✓ synced[/green]"
        table.add_row(name, result, str(report.total_files))
    console.print(table)
    if failures:
        console.print(f"[red]✗ {failures} remote(s) failed[/red]")
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
    if report.skipped:
        console.print(
            f"[yellow]⚠ {len(report.skipped)} file(s) skipped — destination copy is newer:[/yellow]"
        )
        for path in report.skipped:
            console.print(f"  [dim]{path}[/dim]")
    if report.summary and not report.dry_run:
        console.print(done_msg)
    if report.backup:
        console.print(f"[dim]local sessions backed up to {report.backup}[/dim]")


_ARGS_HELP = (
    "Optional remote name (when several are configured), then project selectors — "
    "paths (e.g. '.') or encoded names from status. Default: all projects."
)
_ALL_HELP = "Sync every configured remote; keep going if one fails."


def _both_remote_and_all(cfg: Config, args: list[str]) -> None:
    if args and args[0] in cfg.remotes:
        raise _fail(SyncError(f"give a remote name ({args[0]!r}) or --all, not both"))


@app.command()
def push(
    args: list[str] | None = typer.Argument(
        None, metavar="[REMOTE] [PROJECTS]...", help=_ARGS_HELP
    ),
    all_remotes: bool = typer.Option(False, "--all", help=_ALL_HELP),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would sync."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Answer yes to prompts."),
    force: bool = typer.Option(False, "--force", help="Sync even if Claude Code is running."),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="List files skipped because the destination was newer."
    ),
) -> None:
    """Send local sessions to a remote machine (merge, newer file wins)."""
    cfg = _load_config()
    _refuse_if_running(force)
    home = _local_home()
    if all_remotes:
        _both_remote_and_all(cfg, args or [])
        selectors = args or []
        _guard_remote_typos(cfg, selectors)
        _run_all(
            cfg,
            "push",
            lambda name: sync_mod.push(
                cfg,
                remote=name,
                local_home=home,
                projects=selectors or None,
                dry_run=dry_run,
                force=True,
                verbose=verbose,
                log=_prefixed_log(name),
            ),
        )
        return
    remote, selectors = _split_remote_args(cfg, args or [])
    try:
        report = sync_mod.push(
            cfg,
            remote=remote,
            local_home=home,
            projects=selectors or None,
            dry_run=dry_run,
            force=True,
            verbose=verbose,
            log=_log,
        )
    except _FAILURES as e:
        raise _fail(e) from e
    _render_report(report, "run [bold]claude --resume[/bold] on the other machine")


@app.command()
def pull(
    args: list[str] | None = typer.Argument(
        None, metavar="[REMOTE] [PROJECTS]...", help=_ARGS_HELP
    ),
    all_remotes: bool = typer.Option(False, "--all", help=_ALL_HELP),
    dry_run: bool = typer.Option(False, "--dry-run", "-n", help="Show what would sync."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Answer yes to prompts."),
    force: bool = typer.Option(False, "--force", help="Sync even if Claude Code is running."),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", help="List files skipped because the destination was newer."
    ),
) -> None:
    """Fetch a remote's sessions and merge them in (merge, newer file wins)."""
    cfg = _load_config()
    _refuse_if_running(force)
    home = _local_home()
    confirm = (lambda _msg: True) if yes else (lambda msg: typer.confirm(msg, default=True))
    if all_remotes:
        _both_remote_and_all(cfg, args or [])
        selectors = args or []
        _guard_remote_typos(cfg, selectors)
        _run_all(
            cfg,
            "pull",
            lambda name: sync_mod.pull(
                cfg,
                remote=name,
                local_home=home,
                projects=selectors or None,
                dry_run=dry_run,
                force=True,
                verbose=verbose,
                confirm=confirm,
                log=_prefixed_log(name),
            ),
        )
        return
    remote, selectors = _split_remote_args(cfg, args or [])
    try:
        report = sync_mod.pull(
            cfg,
            remote=remote,
            local_home=home,
            projects=selectors or None,
            dry_run=dry_run,
            force=True,
            verbose=verbose,
            confirm=confirm,
            log=_log,
        )
    except _FAILURES as e:
        raise _fail(e) from e
    _render_report(report, "run [bold]claude --resume[/bold] to pick up where you left off")


@app.command()
def diff(
    args: list[str] | None = typer.Argument(
        None, metavar="[REMOTE] [PROJECTS]...", help=_ARGS_HELP
    ),
) -> None:
    """Show what would sync in each direction, without writing anything."""
    cfg = _load_config()
    home = _local_home()
    remote, selectors = _split_remote_args(cfg, args or [])
    try:
        target = resolve_remote(cfg, remote)
    except _FAILURES as e:
        raise _fail(e) from e
    selection = selectors or None
    where = f"{target.name} ({target.host or target.home})"
    console.print(f"[bold]Outgoing[/bold] (push → {where})")
    try:
        _summary_block(
            sync_mod.push(
                cfg,
                remote=target.name,
                local_home=home,
                projects=selection,
                dry_run=True,
                force=True,
            )
        )
    except _FAILURES as e:
        console.print(f"[yellow]~[/yellow] {e}")
    console.print(f"\n[bold]Incoming[/bold] (pull ← {where})")
    try:
        _summary_block(
            sync_mod.pull(
                cfg,
                remote=target.name,
                local_home=home,
                projects=selection,
                dry_run=True,
                force=True,
            )
        )
    except _FAILURES as e:
        console.print(f"[yellow]~[/yellow] {e}")


@app.command()
def doctor(
    remote: str | None = typer.Argument(
        None, help="Check a single remote (default: every configured remote)."
    ),
) -> None:
    """Check SSH, rsync, config, and local state for problems."""
    from claude_hop import doctor as doctor_mod

    try:
        checks = doctor_mod.run_checks(_state["config_path"], _local_home(), remote)
    except _FAILURES as e:
        raise _fail(e) from e
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


@remotes_app.callback(invoke_without_command=True)
def _remotes_root(ctx: typer.Context) -> None:
    if ctx.invoked_subcommand is None:
        _print_remotes(check=False)


@remotes_app.command("list")
def remotes_list(
    check: bool = typer.Option(False, "--check", help="Probe each remote over SSH."),
) -> None:
    """Show configured remotes (default when no subcommand is given)."""
    _print_remotes(check)


def _print_remotes(check: bool) -> None:
    cfg = _load_config()
    statuses = (
        _probe_remotes(cfg) if check else dict.fromkeys(cfg.remotes, "[dim]—[/dim]")
    )
    table = Table(title="Remotes")
    table.add_column("Name", style="bold")
    table.add_column("Host")
    table.add_column("Home", overflow="fold")
    table.add_column("Mappings", justify="right")
    table.add_column("Status")
    for r in cfg.remotes.values():
        table.add_row(
            r.name,
            r.host or "[dim](local path)[/dim]",
            r.home,
            str(len(cfg.merged_mappings(r.name))),
            statuses[r.name],
        )
    console.print(table)
    if not cfg.remotes:
        console.print("[yellow]no remotes configured — run claude-hop init[/yellow]")


def _probe_remotes(cfg: Config) -> dict[str, str]:
    """SSH-probe every remote in parallel with a short timeout."""
    from concurrent.futures import ThreadPoolExecutor

    def probe(remote: Remote) -> str:
        if not remote.host:
            return "local"
        result = probe_ssh(remote.host)
        if result.verdict == PROBE_OK:
            return "[green]✓ reachable[/green]"
        if result.verdict == PROBE_NO_AUTH:
            return "[yellow]✗ no auth[/yellow]"
        return "[red]✗ unreachable[/red]"

    if not cfg.remotes:
        return {}
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(probe, cfg.remotes.values()))
    return dict(zip(cfg.remotes, results, strict=True))


@remotes_app.command()
def add(name: str = typer.Argument(..., help="Name for the new remote.")) -> None:
    """Add a remote via the interactive wizard."""
    cfg = _load_config()
    _require_new_format(cfg)
    _add_remote_to(cfg, name, _config_path())


@remotes_app.command()
def remove(
    name: str = typer.Argument(..., help="Remote to remove."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Skip the confirmation."),
) -> None:
    """Remove a remote from the config."""
    cfg = _load_config()
    _require_new_format(cfg)
    try:
        resolve_remote(cfg, name)
    except _FAILURES as e:
        raise _fail(e) from e
    if not yes:
        typer.confirm(f"Remove remote {name!r}?", abort=True)
    del cfg.remotes[name]
    config_mod.save(cfg, _config_path())
    console.print(f"[green]✓[/green] removed {name!r}")
    if not cfg.remotes:
        console.print(
            "[yellow]⚠ no remotes left — push/pull won't work until you add one[/yellow]"
        )


@remotes_app.command()
def rename(
    old: str = typer.Argument(..., help="Current name."),
    new: str = typer.Argument(..., help="New name."),
) -> None:
    """Rename a remote."""
    cfg = _load_config()
    _require_new_format(cfg)
    try:
        resolve_remote(cfg, old)
    except _FAILURES as e:
        raise _fail(e) from e
    if not config_mod._NAME_RE.match(new):
        raise _fail(ValueError(f"invalid remote name {new!r} — letters, digits, - and _ only"))
    if new in cfg.remotes:
        raise _fail(ConfigError(f"remote {new!r} already exists"))
    renamed: dict[str, Remote] = {}
    for key, value in cfg.remotes.items():
        if key == old:
            renamed[new] = Remote(
                name=new, host=value.host, home=value.home, mappings=value.mappings
            )
        else:
            renamed[key] = value
    cfg.remotes = renamed
    config_mod.save(cfg, _config_path())
    console.print(f"[green]✓[/green] renamed {old!r} → {new!r}")


@remotes_app.command()
def migrate() -> None:
    """Rewrite a legacy single-[remote] config in the multi-remote format."""
    try:
        backup = config_mod.migrate(_state["config_path"])
    except _FAILURES as e:
        raise _fail(e) from e
    console.print(f"[green]✓[/green] migrated {_config_path()}")
    console.print(f"[dim]original kept at {backup}[/dim]")


def main() -> None:
    app()
