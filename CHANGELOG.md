# Changelog

All notable changes to claude-hop are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project
adheres to [Semantic Versioning](https://semver.org/).

## [Unreleased]

## [0.2.0] - 2026-07-07

### Added

- Single-project sync: `push`, `pull`, and `diff` accept optional project
  selectors — filesystem paths (`.`, `~/work/webshop`) or encoded names as
  shown by `status`. Only the selected projects sync; the `[sync]` extras
  are skipped when a selection is given. Unknown selectors fail with a
  closest-match suggestion, and selective pull fetches only the requested
  project directories from the remote.

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
