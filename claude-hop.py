#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.11"
# ///
"""
claude-hop: sync Claude Code sessions between two machines over SSH (no cloud).

Handles the path-remapping problem: Claude Code indexes sessions by absolute
project path, so /home/vasilis/webshop on Linux and /Users/vasilis/webshop on macOS
are treated as different projects. claude-hop rewrites both the session
directory names and the JSONL contents so `claude --resume` works on the
other machine.

Usage:
    claude-hop init          # interactive config setup
    claude-hop status        # show local projects and how they'd map remotely
    claude-hop push          # send local sessions -> remote machine
    claude-hop pull          # fetch remote sessions -> local machine

Config lives at ~/.claude-hop.toml
"""

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import tomllib
from pathlib import Path

CONFIG_PATH = Path.home() / ".claude-hop.toml"
PROJECTS_DIR = Path.home() / ".claude" / "projects"


# ---------------------------------------------------------------- helpers

def die(msg: str, code: int = 1):
    print(f"✗ {msg}", file=sys.stderr)
    sys.exit(code)


def encode_path(p: str) -> str:
    """Claude Code encodes a project path by replacing non-alphanumerics with '-'.
    /home/vasilis/work/webshop -> -home-vasilis-work-webshop
    """
    return re.sub(r"[^A-Za-z0-9]", "-", p)


def load_config() -> dict:
    if not CONFIG_PATH.exists():
        die(f"No config at {CONFIG_PATH}. Run: claude-hop init")
    with open(CONFIG_PATH, "rb") as f:
        return tomllib.load(f)


def build_mappings(cfg: dict, direction: str) -> list[tuple[str, str]]:
    """Return ordered (old, new) string replacements. Longest keys first so
    specific project mappings win over the generic home mapping."""
    local_home = str(Path.home())
    remote_home = cfg["remote"]["home"].rstrip("/")
    pairs = []
    for src, dst in cfg.get("mappings", {}).items():
        pairs.append((src.rstrip("/"), dst.rstrip("/")))
    pairs.append((local_home, remote_home))  # generic fallback, applied last
    if direction == "pull":
        pairs = [(b, a) for a, b in pairs]
    # longest source first so /home/v/work/webshop beats /home/v
    pairs.sort(key=lambda t: len(t[0]), reverse=True)
    return pairs


def remap_string(s: str, pairs: list[tuple[str, str]]) -> str:
    for old, new in pairs:
        s = s.replace(old, new)
    return s


def claude_running() -> bool:
    try:
        out = subprocess.run(
            ["pgrep", "-fl", "claude"], capture_output=True, text=True
        ).stdout
        # match the claude CLI process, not this script or unrelated things
        return any(
            re.search(r"(^|/)claude(\s|$)", line.split(maxsplit=1)[-1])
            for line in out.splitlines()
        )
    except FileNotFoundError:
        return False


def remap_tree(src: Path, dest: Path, pairs: list[tuple[str, str]]):
    """Copy src projects tree into dest, renaming project dirs and rewriting
    path strings inside every .jsonl file."""
    enc_pairs = [(encode_path(a), encode_path(b)) for a, b in pairs]
    dest.mkdir(parents=True, exist_ok=True)
    for proj in sorted(src.iterdir()):
        if not proj.is_dir():
            continue
        new_name = remap_string(proj.name, enc_pairs)
        target = dest / new_name
        target.mkdir(parents=True, exist_ok=True)
        for f in proj.rglob("*"):
            rel = f.relative_to(proj)
            out = target / rel
            if f.is_dir():
                out.mkdir(parents=True, exist_ok=True)
            elif f.suffix == ".jsonl":
                text = f.read_text(encoding="utf-8", errors="replace")
                out.write_text(remap_string(text, pairs), encoding="utf-8")
                shutil.copystat(f, out)  # preserve mtime so rsync -u works
            else:
                shutil.copy2(f, out)


def rsync(src: str, dst: str, ssh_host: str | None = None):
    """Merge-copy with rsync: -u keeps whichever file is newer, never deletes."""
    cmd = ["rsync", "-avu", "--exclude", "*.tmp", src, dst]
    print("→", " ".join(cmd))
    r = subprocess.run(cmd)
    if r.returncode != 0:
        die("rsync failed")


# ---------------------------------------------------------------- commands

def cmd_init():
    print("claude-hop setup\n----------------")
    host = input("SSH host of the other machine (e.g. mac.local or ssh alias): ").strip()
    if not host:
        die("host is required")
    # try to auto-detect the remote home
    remote_home = ""
    try:
        r = subprocess.run(
            ["ssh", host, "echo $HOME"], capture_output=True, text=True, timeout=15
        )
        if r.returncode == 0:
            remote_home = r.stdout.strip()
            print(f"  detected remote home: {remote_home}")
    except Exception:
        pass
    if not remote_home:
        remote_home = input("Remote home directory (e.g. /Users/vasilis): ").strip()

    CONFIG_PATH.write_text(
        f'[remote]\nhost = "{host}"\nhome = "{remote_home}"\n\n'
        "# Optional: explicit project mappings when relative paths differ.\n"
        "# Specific mappings take priority over the generic home remap.\n"
        "# [mappings]\n"
        f'# "{Path.home()}/work/webshop" = "{remote_home}/projects/webshop"\n'
    )
    print(f"✓ wrote {CONFIG_PATH}")
    print("Run `claude-hop status` to check how your projects will map.")


def cmd_status():
    cfg = load_config()
    pairs = build_mappings(cfg, "push")
    enc_pairs = [(encode_path(a), encode_path(b)) for a, b in pairs]
    if not PROJECTS_DIR.exists():
        die(f"{PROJECTS_DIR} does not exist — no local sessions found")
    print(f"Local projects in {PROJECTS_DIR}:\n")
    for proj in sorted(PROJECTS_DIR.iterdir()):
        if not proj.is_dir():
            continue
        mapped = remap_string(proj.name, enc_pairs)
        n = len(list(proj.glob("*.jsonl")))
        marker = "→" if mapped != proj.name else "= (unchanged)"
        print(f"  {proj.name}  ({n} sessions)")
        print(f"    {marker} {mapped}\n")
    print("If a mapping above looks wrong (project lives at a different path")
    print(f"on the remote machine), add it under [mappings] in {CONFIG_PATH}.")


def cmd_push():
    cfg = load_config()
    host = cfg["remote"]["host"]
    if claude_running():
        print("⚠ Claude Code appears to be running. It only writes sessions to")
        print("  disk on exit — quit it first or your latest work won't sync.")
        if input("  Continue anyway? [y/N] ").lower() != "y":
            sys.exit(0)
    pairs = build_mappings(cfg, "push")
    with tempfile.TemporaryDirectory(prefix="claude-hop-") as tmp:
        staging = Path(tmp) / "projects"
        print("• remapping paths into staging…")
        remap_tree(PROJECTS_DIR, staging, pairs)
        print(f"• syncing to {host}…")
        subprocess.run(["ssh", host, "mkdir -p ~/.claude/projects"], check=False)
        rsync(f"{staging}/", f"{host}:~/.claude/projects/")
    print("✓ push complete — run `claude --resume` on the other machine")


def cmd_pull():
    cfg = load_config()
    host = cfg["remote"]["host"]
    pairs = build_mappings(cfg, "pull")
    with tempfile.TemporaryDirectory(prefix="claude-hop-") as tmp:
        raw = Path(tmp) / "raw"
        staging = Path(tmp) / "projects"
        raw.mkdir()
        print(f"• fetching from {host}…")
        rsync(f"{host}:~/.claude/projects/", f"{raw}/")
        print("• remapping paths…")
        remap_tree(raw, staging, pairs)
        print("• merging into local sessions…")
        PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
        rsync(f"{staging}/", f"{PROJECTS_DIR}/")
    print("✓ pull complete — run `claude --resume` to pick up where you left off")


def main():
    cmds = {"init": cmd_init, "status": cmd_status, "push": cmd_push, "pull": cmd_pull}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        print(__doc__.strip())
        sys.exit(1)
    cmds[sys.argv[1]]()


if __name__ == "__main__":
    main()
