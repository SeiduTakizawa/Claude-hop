<p align="center">
  <img src="https://raw.githubusercontent.com/SeiduTakizawa/Claude-hop/main/.github/banner.png"
       alt="claude-hop — sync Claude Code sessions between machines" width="100%">
</p>

<p align="center">
  <a href="https://pypi.org/project/claude-hop/"><img src="https://img.shields.io/pypi/v/claude-hop?color=ff8800" alt="PyPI"></a>
  <a href="https://github.com/SeiduTakizawa/Claude-hop/actions/workflows/ci.yml"><img src="https://github.com/SeiduTakizawa/Claude-hop/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://pypi.org/project/claude-hop/"><img src="https://img.shields.io/pypi/pyversions/claude-hop" alt="Python"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="MIT"></a>
</p>

Start a Claude Code session on your Linux desktop, `claude --resume` it on
your Mac — or any pair of your machines. claude-hop syncs
`~/.claude/projects/` over SSH with rsync, rewriting the absolute paths that
would otherwise make sessions invisible on the other side. No cloud, no
accounts: your machines, your keys, your sessions.

## Quickstart (30 seconds)

```sh
uv tool install claude-hop        # or: pipx install claude-hop
claude-hop init                   # asks for the SSH host, detects the rest
claude-hop push                   # then `claude --resume` on the other machine
```

No `uv`? One-liner that installs it first:

```sh
curl -sSL https://raw.githubusercontent.com/SeiduTakizawa/Claude-hop/main/install.sh | bash
```

Requirements: Python ≥ 3.11 on this machine; `sshd` and `rsync` on the other
one (key-based SSH auth recommended).

## See it in action

<p align="center">
  <img src="https://raw.githubusercontent.com/SeiduTakizawa/Claude-hop/main/assets/claude-hop.gif"
       alt="claude-hop demo: init banner, remotes table, the path remap, push" width="800">
</p>
<p align="center"><em>Real commands against a sandbox — <a href="demo/demo.tape">scripted with VHS</a>, so it re-renders instead of re-recording.</em></p>

## The problem it solves

Claude Code stores sessions in `~/.claude/projects/`, indexed by the
**absolute path** of the project with every non-alphanumeric character
replaced by a dash:

```
/home/alice/work/webshop   →   ~/.claude/projects/-home-alice-work-webshop/
```

The absolute paths also appear *inside* the session JSONL files — `cwd`
fields, file paths in tool calls. Copy the files verbatim from a Linux box
(`/home/alice`) to a Mac (`/Users/alice`) and `claude --resume` can never
find them: the directory name encodes the wrong path and the transcript
still references the wrong filesystem.

claude-hop rewrites **both** during sync — directory names and JSONL
contents — mapping your home here to your home there (auto-detected at
`init`), in whichever direction you sync. The rewrite is boundary-aware
(`/home/al` never matches inside `/home/alice`) and round-trip safe: push
then pull reproduces your files byte for byte.

## Commands

| Command | What it does |
|---|---|
| `claude-hop init` | Interactive setup: SSH host, connection test, remote home auto-detection, rsync checks. With an existing config it offers to add another remote. |
| `claude-hop status [REMOTE]` | Table of local projects, session counts, and the remapped name each gets on the remote; flags ambiguous mappings. |
| `claude-hop diff [REMOTE]` | What would sync, in both directions, without writing anything. |
| `claude-hop push [REMOTE]` | Send local sessions to a remote machine. `--all` pushes to every remote. |
| `claude-hop pull [REMOTE]` | Fetch a remote's sessions and merge them in. `--all` pulls from every remote. |
| `claude-hop doctor [REMOTE]` | Environment checks: SSH, rsync versions, config, running Claude Code, stale staging dirs. Checks every remote by default. |
| `claude-hop remotes` | List configured remotes (`--check` probes them over SSH). |
| `claude-hop remotes add/remove/rename` | Manage remotes; `add` runs the same wizard as init. |
| `claude-hop remotes migrate` | Upgrade an old single-remote config to the multi-remote format (backs up the original first). |
| `claude-hop upgrade` | Upgrade claude-hop itself to the latest release (works for uv, pipx, and pip installs). |

`push` and `pull` accept `--dry-run` (`-n`), `--yes` (`-y`), `--force`, and `--all`.

**Which remote runs?** An explicit name wins (`claude-hop push mac`); with
exactly one remote configured the name can be omitted; with several and no
name, commands refuse and list your options — nothing is picked implicitly.
The first positional argument counts as a remote only when it exactly
matches a configured remote name; anything else is a project selector, and
a near-miss of a remote name is an error with a suggestion rather than a
silent no-op. If a directory in your cwd shares a remote's name, write
`./name` to mean the project.

**Multiple machines are hub-and-spoke.** This machine is the operator;
remotes only need `sshd` and `rsync`, and remotes never talk to each other.
To move sessions between two remotes, route through the hub —
`claude-hop pull mac` then `claude-hop push thinkpad` — the merge semantics
(newer file wins, never deletes) make that safe in any order.

### Syncing a single project

`push`, `pull`, and `diff` take optional project selectors — by default they
sync everything:

```sh
claude-hop push .                             # just the project you're standing in
claude-hop pull ~/work/webshop                # one project, by path
claude-hop push -- -home-alice-work-webshop   # by encoded name, as shown in `status`
claude-hop diff . ~/work/blog                 # several at once
```

Encoded names start with a dash, so pass them after the standard `--`
separator — everything after `--` is a selector, never an option.

Selectors are asymmetric on purpose: `push` matches them against your
*local* projects, while `pull` translates them through the mapping and
matches against the *remote's* projects — so pulling a project that doesn't
exist locally yet works.

When a selection is given, only those projects sync and the optional `[sync]`
extras (history/agents/skills) are skipped.

## Safety model

- **Merge, never clobber.** rsync runs with `-u` (newer file wins) and never
  with `--delete` — a sync can only add or update sessions, never remove.
- **Staging, not in-place.** Remapping happens in a temp dir;
  `~/.claude/projects/` is only ever touched by the merge itself.
- **Refuses to run alongside Claude Code.** Sessions are flushed to disk on
  exit, so syncing mid-session would miss your latest work. Override with
  `--force` if you know what you're doing.
- **First-pull backup.** The first pull that would merge into existing local
  sessions offers a tarball backup (`~/.claude/backups/`) before touching
  anything.

## Configuration

`~/.config/claude-hop/config.toml` (written by `init`, `$XDG_CONFIG_HOME`
honoured). Each machine you sync with is a named `[remotes.<name>]` table:

```toml
[remotes.mac]
host = "mac.local"          # SSH alias or hostname; "" = a local directory
home = "/Users/alice"       # auto-detected at init

[remotes.thinkpad]
host = "192.168.1.50"
home = "/home/al"

# per-remote mappings: merged over the global [mappings], per-remote wins
[remotes.mac.mappings]
"/home/alice/work/webshop" = "/Users/alice/projects/webshop"

[sync]                      # applies to all remotes
include_history = false     # also sync ~/.claude/history.jsonl (remapped)
include_agents = false      # also sync ~/.claude/agents/ (verbatim)
include_skills = false      # also sync ~/.claude/skills/ (verbatim)

[mappings]
# Global: projects at *different relative paths* on the other machines.
# Specific mappings win over the generic home remap.
"/home/alice/work/blog" = "/srv/blog"
```

Configs from claude-hop ≤ 0.2.0 (a single `[remote]` table) keep working
and are read as a remote named `default`; run `claude-hop remotes migrate`
to upgrade the file in place (the original is backed up first).

`claude-hop status` shows exactly how every project will map; if one looks
wrong, add it under `[mappings]` (or the remote's own mappings table).

## Supported platforms

Any pair of Unix-shaped machines, in either direction: **Linux ↔ macOS**,
**Linux ↔ Linux**, **macOS ↔ macOS**. The remap isn't tied to `/home` vs
`/Users` — it maps *your home here* to *your home there*, whatever they are
(identical homes on both sides work too; the remap just becomes a no-op and
you keep the merge/safety machinery).

**Windows** is supported **via WSL**: inside WSL you have a real Linux home,
`rsync`, and `sshd`, so claude-hop works there like on any Linux box — it
syncs the Claude Code you run *inside* WSL. Native (non-WSL) Windows is not
supported: Claude Code encodes `C:\` project paths differently and there's
no native rsync. If you need it, open an issue.

## Limitations worth knowing

- Sessions whose *conversation text* literally mentions the other machine's
  home path (e.g. a transcript discussing `/Users/alice/...` while on the
  Linux box) can't be rewritten losslessly — path rewriting is text-level by
  design, so such mentions get remapped too.
- Session files that aren't valid UTF-8 are copied verbatim without
  remapping rather than risk corrupting them.
- On macOS 15+, the bundled `rsync` is `openrsync`; it works, but if you hit
  option errors, `brew install rsync`.

## Development

```sh
uv sync           # install with dev dependencies
uv run pytest     # unit + integration tests (integration needs rsync)
uv run ruff check .
make e2e          # full two-container SSH round trip (needs Docker)
```

The e2e rig (`e2e/`) spins up two containers — one with a Linux-style home,
one macOS-style — wires SSH key auth between them, and drives the real CLI
through push, modify-on-the-other-side, and pull.

### Regenerating the demo

The README GIF is scripted, not hand-recorded: `demo/setup.sh` builds a
self-contained sandbox (fake home, two local-path remotes) and
`demo/demo.tape` replays real commands against it with
[VHS](https://github.com/charmbracelet/vhs). When CLI output changes, run
`make demo` (needs `vhs`, `ttyd`, `ffmpeg`) and commit the refreshed
`assets/claude-hop.gif`.

## License

MIT — see [LICENSE](LICENSE).
