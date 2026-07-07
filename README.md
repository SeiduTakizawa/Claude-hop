<p align="center">
  <img src="https://raw.githubusercontent.com/SeiduTakizawa/Claude-hop/main/.github/banner.png"
       alt="claude-hop — sync Claude Code sessions between machines" width="100%">
</p>

Sync Claude Code sessions between two machines over SSH — no cloud storage,
just rsync between your own boxes. Start a session on your Linux desktop,
`claude --resume` it on your Mac (or the other way round).

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

## The problem it solves

Claude Code stores sessions in `~/.claude/projects/`, indexed by the
**absolute path** of the project with every non-alphanumeric character
replaced by a dash:

```
/home/alice/work/webshop   →   ~/.claude/projects/-home-alice-work-webshop/
```

The absolute paths also appear *inside* the session JSONL files — `cwd`
fields, file paths in tool calls. So copying the files verbatim from a Linux
box (`/home/alice`) to a Mac (`/Users/alice`) produces sessions that
`claude --resume` can never find: the directory name encodes the wrong path
and the transcript still references the wrong filesystem.

claude-hop rewrites **both** during sync — directory names and JSONL
contents — mapping your local home to the remote home (auto-detected at
`init`), in whichever direction you sync. The rewrite is boundary-aware
(`/home/al` never matches inside `/home/alice`) and round-trip safe: push
then pull reproduces your files byte for byte.

## Commands

| Command | What it does |
|---|---|
| `claude-hop init` | Interactive setup: SSH host, connection test, remote home auto-detection, rsync checks. |
| `claude-hop status` | Table of local projects, session counts, and the remapped name each gets on the remote; flags ambiguous mappings. |
| `claude-hop diff` | What would sync, in both directions, without writing anything. |
| `claude-hop push` | Send local sessions to the remote machine. |
| `claude-hop pull` | Fetch remote sessions and merge them in. |
| `claude-hop doctor` | Environment checks: SSH, rsync versions, config, running Claude Code, stale staging dirs. |
| `claude-hop upgrade` | Upgrade claude-hop itself to the latest release (works for uv, pipx, and pip installs). |

`push` and `pull` accept `--dry-run` (`-n`), `--yes` (`-y`), and `--force`.

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
honoured):

```toml
[remote]
host = "mac.local"          # SSH alias or hostname; "" = a local directory
home = "/Users/alice"       # auto-detected at init

[sync]
include_history = false     # also sync ~/.claude/history.jsonl (remapped)
include_agents = false      # also sync ~/.claude/agents/ (verbatim)
include_skills = false      # also sync ~/.claude/skills/ (verbatim)

[mappings]
# For projects that live at *different relative paths* on the two machines.
# Specific mappings win over the generic home remap.
"/home/alice/work/webshop" = "/Users/alice/projects/webshop"
```

`claude-hop status` shows exactly how every project will map; if one looks
wrong, add it under `[mappings]`.

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

## License

MIT — see [LICENSE](LICENSE).
