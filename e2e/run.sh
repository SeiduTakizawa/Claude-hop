#!/usr/bin/env bash
# End-to-end test: two containers (Linux-style and macOS-style homes) with
# sshd + rsync + key auth. Pushes real sessions from "linux" to "mac",
# asserts the remap, modifies on "mac", pulls back, asserts the merge.
set -euo pipefail
cd "$(dirname "$0")"

step() { printf '\n\033[1m--- %s\033[0m\n' "$*"; }

cleanup() { docker compose down -v --remove-orphans >/dev/null 2>&1 || true; }
trap cleanup EXIT

# run a command as the tester user on a container
lx() { docker compose exec -T -u tester linux bash -c "$1"; }
mc() { docker compose exec -T -u tester mac bash -c "$1"; }

step "build and start containers"
docker compose up -d --build --quiet-pull

step "set up key auth linux -> mac"
lx 'mkdir -p ~/.ssh && chmod 700 ~/.ssh &&
    [ -f ~/.ssh/id_ed25519 ] || ssh-keygen -q -t ed25519 -N "" -f ~/.ssh/id_ed25519'
PUB=$(lx 'cat ~/.ssh/id_ed25519.pub')
mc "mkdir -p ~/.ssh && chmod 700 ~/.ssh &&
    printf '%s\n' '$PUB' >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
lx 'for i in $(seq 1 30); do
      ssh -o StrictHostKeyChecking=accept-new -o ConnectTimeout=2 mac true 2>/dev/null && exit 0
      sleep 1
    done
    echo "sshd on mac unreachable" >&2; exit 1'

step "seed sessions and LEGACY-format config on linux"
lx 'mkdir -p ~/.claude/projects/-home-tester-work-webshop &&
    printf "%s\n" "{\"cwd\":\"/home/tester/work/webshop\",\"msg\":\"from linux\"}" \
      > ~/.claude/projects/-home-tester-work-webshop/s1.jsonl'
lx 'mkdir -p ~/.config/claude-hop &&
    printf "[remote]\nhost = \"mac\"\nhome = \"/Users/tester\"\n" \
      > ~/.config/claude-hop/config.toml'

step "legacy pass: doctor / status / push work on the old format"
lx 'claude-hop doctor'
lx 'claude-hop status'
lx 'claude-hop push --yes'
lx 'claude-hop status 2>&1 >/dev/null | grep -q "run claude-hop remotes migrate to upgrade it"'
echo "ok: legacy config works and hints at migration on stderr"

step "migrate to the multi-remote format"
lx 'claude-hop remotes migrate'
lx 'test -f ~/.config/claude-hop/config.toml.bak'
lx 'grep -q "\[remotes.default\]" ~/.config/claude-hop/config.toml'
lx 'claude-hop remotes'
echo "ok: migrated; everything below runs on the new format"

step "assert remapped sessions on mac"
mc 'test -f ~/.claude/projects/-Users-tester-work-webshop/s1.jsonl'
mc 'grep -q "\"cwd\":\"/Users/tester/work/webshop\"" ~/.claude/projects/-Users-tester-work-webshop/s1.jsonl'
mc '! grep -rq "/home/tester" ~/.claude/projects'
echo "ok: dirs and JSONL remapped, no /home/tester leakage"

step "modify on mac: new session in the same project + a new project"
mc 'printf "%s\n" "{\"cwd\":\"/Users/tester/work/webshop\",\"msg\":\"from mac\"}" \
      > ~/.claude/projects/-Users-tester-work-webshop/s2.jsonl'
mc 'mkdir -p ~/.claude/projects/-Users-tester-other-beta &&
    printf "%s\n" "{\"cwd\":\"/Users/tester/other/beta\"}" \
      > ~/.claude/projects/-Users-tester-other-beta/s1.jsonl'

step "claude-hop pull on linux"
lx 'claude-hop pull --yes'

step "assert merge on linux"
lx 'test -f ~/.claude/projects/-home-tester-work-webshop/s1.jsonl'
lx 'test -f ~/.claude/projects/-home-tester-work-webshop/s2.jsonl'
lx 'grep -q "\"cwd\":\"/home/tester/work/webshop\"" ~/.claude/projects/-home-tester-work-webshop/s2.jsonl'
lx 'test -f ~/.claude/projects/-home-tester-other-beta/s1.jsonl'
lx 'grep -q "\"cwd\":\"/home/tester/other/beta\"" ~/.claude/projects/-home-tester-other-beta/s1.jsonl'
lx '! grep -rq "/Users/tester" ~/.claude/projects'
echo "ok: pull merged and remapped, no /Users/tester leakage"

step "second round trip: a session resumed on mac must come back"
mc 'printf "%s\n" "{\"cwd\":\"/Users/tester/work/webshop\",\"msg\":\"resumed on mac\"}" \
      >> ~/.claude/projects/-Users-tester-work-webshop/s2.jsonl'
lx 'claude-hop pull --yes'
lx 'grep -q "resumed on mac" ~/.claude/projects/-home-tester-work-webshop/s2.jsonl'
lx 'grep -q "{\"cwd\":\"/home/tester/work/webshop\",\"msg\":\"resumed on mac\"}" \
      ~/.claude/projects/-home-tester-work-webshop/s2.jsonl'
echo "ok: resumed-session update arrived remapped on the second pull"

step "claude-hop diff (both directions should be in sync)"
DIFF_OUT=$(lx 'claude-hop diff')
echo "$DIFF_OUT"
[ "$(grep -c 'already in sync' <<<"$DIFF_OUT")" -eq 2 ]

step "single-project push (gamma crosses, delta must not)"
lx 'mkdir -p ~/.claude/projects/-home-tester-work-gamma ~/.claude/projects/-home-tester-work-delta &&
    printf "%s\n" "{\"cwd\":\"/home/tester/work/gamma\"}" \
      > ~/.claude/projects/-home-tester-work-gamma/s1.jsonl &&
    printf "%s\n" "{\"cwd\":\"/home/tester/work/delta\"}" \
      > ~/.claude/projects/-home-tester-work-delta/s1.jsonl'
lx 'claude-hop push --yes /home/tester/work/gamma'
mc 'test -f ~/.claude/projects/-Users-tester-work-gamma/s1.jsonl'
mc '! test -e ~/.claude/projects/-Users-tester-work-delta'
echo "ok: single-project push transferred only gamma"

step "single-project pull (epsilon exists only on mac)"
mc 'mkdir -p ~/.claude/projects/-Users-tester-work-epsilon &&
    printf "%s\n" "{\"cwd\":\"/Users/tester/work/epsilon\"}" \
      > ~/.claude/projects/-Users-tester-work-epsilon/s1.jsonl'
lx 'claude-hop pull --yes /home/tester/work/epsilon'
lx 'grep -q "\"cwd\":\"/home/tester/work/epsilon\"" ~/.claude/projects/-home-tester-work-epsilon/s1.jsonl'
echo "ok: single-project pull fetched only epsilon"

printf '\n\033[32mE2E OK\033[0m\n'
