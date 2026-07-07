# Changelog

All notable changes to claude-hop are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.4.0] - 2026-07-07

The "field feedback" release: every friction point reported from real
multi-remote, WSL, and first-round-trip setups.

### Merge integrity

- Verified end to end that rewritten session files always carry the
  original mtime (nanosecond-exact through the remap step; second-round-
  trip covered in e2e) — newer-wins merging depends on it.
- `doctor` checks clock skew against each SSH remote and warns above 30s
  that newer-wins merging may skip updates.
- `push`/`pull --verbose` lists files a merge skipped because the
  destination copy was newer, so silent skips are visible.

### Truthful SSH probes

- All probes (init wizard, doctor, `remotes list --check`) run with
  BatchMode and a connect timeout — they can never hang on an invisible
  password prompt.
- Outcomes are classified: auth failure reports "host reachable, but
  key-based auth isn't set up — run: ssh-copy-id <host>" instead of a
  bogus timeout; timeout, DNS failure, and refused/no-route are each
  reported as what they are. `remotes list --check` distinguishes
  ✓ reachable / ✗ no auth / ✗ unreachable / local.

### Projects outside the home directory (WSL)

- `status` flags projects the home remap can't place (e.g. `/mnt/c/...`)
  instead of showing a misleading "unchanged".
- `push`/`pull` refuse such projects with a ready-to-paste `[mappings]`
  snippet computed from the projects' real paths (recovered from session
  cwd fields and validated against the directory name); `--force`
  overrides. `init`/`remotes add` offer to add the prefix mapping
  interactively; `doctor` gains a project-coverage check.

### Git awareness

- `push` warns (stderr, advisory only) when a synced project's repo has
  uncommitted changes or commits not pushed to any upstream — session
  context arrives before the code does. Suppress with `--quiet-git` or
  `--yes`. `doctor` gains the same scan.

### Session-store sanity

- `doctor` notes other users' `~/.claude` session stores on the machine
  and warns specifically when running as root while non-root stores
  exist (claude-hop only syncs the current user's store).

### Fixed

- `claude-hop upgrade` on uv installs now bypasses uv's index cache
  (`uv tool upgrade --reinstall`); right after a release the cache could
  report "Nothing to upgrade" for a version PyPI already serves.

## [0.3.0] - 2026-07-07

### Added

- Multiple remotes: named `[remotes.<name>]` config tables with optional
  per-remote mappings merged over the global `[mappings]`. `push`, `pull`,
  `diff`, and `status` take an optional REMOTE argument; `--all` on
  push/pull syncs every remote (continuing past failures, with a summary
  table); `doctor` checks every remote by default.
- `claude-hop remotes` command group: `list` (with `--check` SSH probing),
  `add`, `remove`, `rename`, and `migrate` for upgrading old single-remote
  configs (backed up first, never auto-migrated). Old configs keep working
  as a remote named `default`.
- `init` with an existing config now offers to add another remote instead
  of overwriting.

## [0.2.0] - 2026-07-07

### Added

- Single-project sync: `push`, `pull`, and `diff` accept optional project
  selectors — filesystem paths (`.`, `~/work/webshop`) or encoded names as
  shown by `status`. Only the selected projects sync; the `[sync]` extras
  are skipped when a selection is given. Unknown selectors fail with a
  closest-match suggestion, and selective pull fetches only the requested
  project directories from the remote.
- `claude-hop upgrade`: self-upgrade to the latest PyPI release, detecting
  whether the tool was installed via uv, pipx, or pip; refuses on
  development checkouts.
- ASCII-art startup banner on `claude-hop init` (terminal ≥ 80 columns
  only; plain one-line header when piped or narrow).

## [0.1.0] - 2026-07-07

Initial release.

### Added

- Path remap engine: rewrites both encoded project directory names and
  absolute paths inside session JSONL files, in either direction, with
  boundary-aware single-pass substitution and round-trip identity.
- `claude-hop init` — interactive setup: SSH connection test, remote home
  auto-detection, rsync checks, config written to
  `~/.config/claude-hop/config.toml`.
- `claude-hop status` — projects table with remapped names; flags
  ambiguous mappings and collisions.
- `claude-hop diff` — dry-run summary of what would sync, per project,
  in both directions.
- `claude-hop push` / `claude-hop pull` — merge sync over SSH (rsync,
  newer file wins, never deletes) with `--dry-run`, `--yes`, `--force`.
- `claude-hop doctor` — environment checks: SSH, rsync versions, config,
  running Claude Code, stale staging dirs.
- Safety: staging-dir remapping (never mutates `~/.claude/projects` in
  place), refusal to sync while Claude Code is running, tarball backup
  offer on first pull into existing sessions.
- Optional `[mappings]` config table for projects at different relative
  paths on the two machines.
- Local-path mode (`host = ""`) for syncing to a mounted directory.
- Docker-based end-to-end test rig (`make e2e`) and GitHub Actions CI.
