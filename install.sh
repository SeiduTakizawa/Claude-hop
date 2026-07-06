#!/usr/bin/env sh
# claude-hop installer — installs uv if missing, then claude-hop as a uv tool.
#
#   curl -sSL https://raw.githubusercontent.com/OWNER/claude-hop/main/install.sh | bash
#
# Override the package source (e.g. to install from git or a local checkout):
#   CLAUDE_HOP_SOURCE="git+https://github.com/OWNER/claude-hop" ./install.sh
set -eu

SOURCE="${CLAUDE_HOP_SOURCE:-claude-hop}"

say() { printf '%s\n' "$*"; }

if ! command -v uv >/dev/null 2>&1; then
    say "uv not found — installing it first (https://docs.astral.sh/uv)…"
    if command -v curl >/dev/null 2>&1; then
        curl -LsSf https://astral.sh/uv/install.sh | sh
    elif command -v wget >/dev/null 2>&1; then
        wget -qO- https://astral.sh/uv/install.sh | sh
    else
        say "error: need curl or wget to install uv" >&2
        exit 1
    fi
    # the uv installer puts the binary here; make it visible to this script
    PATH="$HOME/.local/bin:$PATH"
    export PATH
fi

say "installing claude-hop from ${SOURCE}…"
uv tool install --force "$SOURCE"

if command -v claude-hop >/dev/null 2>&1; then
    say "✓ $(claude-hop --version) installed"
else
    say "✓ installed — if 'claude-hop' is not on your PATH yet, run: uv tool update-shell"
fi
say "next step: claude-hop init"
