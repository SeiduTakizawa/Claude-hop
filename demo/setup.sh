#!/usr/bin/env bash
# Build the self-contained sandbox for the README demo GIF.
#
# Everything lives physically under demo/.sandbox/ (gitignored); a symlink
# at /tmp/hop-demo gives the recording short, neutral paths that leak no
# real usernames or directories:
#
#   /tmp/hop-demo/home/demo        the "local" machine's HOME
#   /tmp/hop-demo/Users/demo       fake remote "mac"      (host = "")
#   /tmp/hop-demo/home/demo2       fake remote "thinkpad" (host = "")
#   /tmp/hop-demo/xdg              XDG_CONFIG_HOME with the demo config
#   /tmp/hop-demo/bin              pgrep stub: the sandbox machine has no
#                                  Claude Code running
#
# Idempotent: wipes and rebuilds the sandbox on every run. The demo shell
# must export:
#   export HOME=/tmp/hop-demo/home/demo
#   export XDG_CONFIG_HOME=/tmp/hop-demo/xdg
#   export PATH=/tmp/hop-demo/bin:$PATH
set -euo pipefail

cd "$(dirname "$0")"
SANDBOX_DIR="$PWD/.sandbox"
LINK="/tmp/hop-demo"

rm -rf "$SANDBOX_DIR"
mkdir -p "$SANDBOX_DIR"
ln -sfn "$SANDBOX_DIR" "$LINK"

DEMO_HOME="$LINK/home/demo"
PROJECTS="$DEMO_HOME/.claude/projects"
mkdir -p "$PROJECTS" "$LINK/Users/demo" "$LINK/home/demo2" "$LINK/xdg" "$LINK/bin"

encode() { printf '%s' "$1" | sed 's/[^A-Za-z0-9]/-/g'; }

# jsonl <cwd> <sessionfile> <user text> <assistant text>...
session() {
    local cwd="$1" file="$2"
    shift 2
    local dir
    dir="$PROJECTS/$(encode "$cwd")"
    mkdir -p "$dir"
    : > "$dir/$file"
    local role=user text
    for text in "$@"; do
        printf '{"type":"%s","cwd":"%s","message":{"role":"%s","content":"%s"}}\n' \
            "$role" "$cwd" "$role" "$text" >> "$dir/$file"
        if [ "$role" = user ]; then role=assistant; else role=user; fi
    done
}

WEBSHOP="$DEMO_HOME/work/webshop"
API="$DEMO_HOME/side/api"
DOTFILES="$DEMO_HOME/dotfiles"

session "$WEBSHOP" "0f31c2a4.jsonl" \
    "add a coupon model and apply it to the checkout total" \
    "Added Coupon in models.py and wired it into checkout(); tests pass."
session "$WEBSHOP" "8d02be71.jsonl" \
    "why is the cart page slow?" \
    "The cart query loads every product row; added select_related and a limit."
session "$WEBSHOP" "c59a1e88.jsonl" \
    "write a migration for the coupons table" \
    "Created 0042_coupon.py and ran it against the dev database."
session "$API" "2b6f9ad3.jsonl" \
    "sketch a rate limiter middleware" \
    "Added a sliding-window limiter keyed on the API token."
session "$API" "e4d7c015.jsonl" \
    "add pagination to /v1/orders" \
    "Cursor-based pagination is in; docs updated."
session "$DOTFILES" "77a0f3b9.jsonl" \
    "port my zsh aliases to fish" \
    "Converted aliases.zsh to conf.d/aliases.fish."

mkdir -p "$LINK/xdg/claude-hop"
cat > "$LINK/xdg/claude-hop/config.toml" <<EOF
[remotes]

[remotes.mac]
host = ""
home = "$LINK/Users/demo"

[remotes.thinkpad]
host = ""
home = "$LINK/home/demo2"

[sync]
include_history = false
include_agents = false
include_skills = false

[mappings]
EOF

# The sandbox "machine" has no Claude Code running: stub out pgrep so the
# real running-check answers honestly for the sandbox instead of finding
# the Claude session that is rendering this demo.
cat > "$LINK/bin/pgrep" <<'EOF'
#!/bin/sh
exit 1
EOF
chmod +x "$LINK/bin/pgrep"

echo "sandbox ready at $LINK -> $SANDBOX_DIR"
