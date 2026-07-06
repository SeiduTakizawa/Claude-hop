# claude-hop

Sync Claude Code sessions between two machines over SSH — no cloud storage,
just rsync between your own boxes.

> **Status: work in progress.** Core remap engine and config are done;
> CLI, SSH transport, and packaging are landing next. The full quickstart
> README arrives with the first release.

## The problem it solves

Claude Code stores sessions in `~/.claude/projects/`, indexed by the
**absolute path** of the project with non-alphanumerics replaced by dashes
(`/home/alice/work/webshop` → `-home-alice-work-webshop`). The absolute paths also
appear *inside* the session JSONL files. Copying the files verbatim from a
Linux box (`/home/alice`) to a Mac (`/Users/alice`) produces sessions that
`claude --resume` can never find. claude-hop rewrites both the directory
names and the JSONL contents during sync, in both directions.

## Development

```sh
uv sync          # install with dev dependencies
uv run pytest    # unit tests
uv run ruff check
```
