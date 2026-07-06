"""Config schema, XDG path resolution, and TOML load/save.

Config lives at ``~/.config/claude-hop/config.toml`` (``$XDG_CONFIG_HOME``
honoured). ``remote.host = ""`` selects local-path mode, where
``remote.home`` is a directory on this machine — used by the integration
tests and useful for syncing to a mounted disk.
"""

from __future__ import annotations

import json
import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_SYNC_FLAGS = ("include_history", "include_agents", "include_skills")


class ConfigError(Exception):
    """Invalid, missing, or unparseable configuration."""


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "claude-hop" / "config.toml"


@dataclass
class Config:
    host: str
    remote_home: str
    include_history: bool = False
    include_agents: bool = False
    include_skills: bool = False
    mappings: dict[str, str] = field(default_factory=dict)


def load(path: str | Path | None = None) -> Config:
    path = Path(path) if path is not None else default_config_path()
    if not path.exists():
        raise ConfigError(f"no config at {path} — run: claude-hop init")
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e
    return parse(data)


def parse(data: dict) -> Config:
    remote = data.get("remote")
    if not isinstance(remote, dict):
        raise ConfigError("missing [remote] table")
    host = remote.get("host", "")
    if not isinstance(host, str):
        raise ConfigError("remote.host must be a string")
    home = remote.get("home")
    if not isinstance(home, str) or not home:
        raise ConfigError("remote.home is required")
    home = _normalize_abs(home, "remote.home")

    sync = data.get("sync", {})
    if not isinstance(sync, dict):
        raise ConfigError("[sync] must be a table")
    flags = {}
    for key in _SYNC_FLAGS:
        value = sync.get(key, False)
        if not isinstance(value, bool):
            raise ConfigError(f"sync.{key} must be true or false")
        flags[key] = value

    raw_mappings = data.get("mappings", {})
    if not isinstance(raw_mappings, dict):
        raise ConfigError("[mappings] must be a table")
    mappings: dict[str, str] = {}
    seen_dst: dict[str, str] = {}
    for src, dst in raw_mappings.items():
        if not isinstance(dst, str):
            raise ConfigError(f"mapping for {src!r} must be a string path")
        src_n = _normalize_abs(src, f"mapping source {src!r}")
        dst_n = _normalize_abs(dst, f"mapping target {dst!r}")
        if src_n in mappings:
            raise ConfigError(f"duplicate mapping source {src_n!r}")
        if dst_n in seen_dst:
            raise ConfigError(
                f"mapping targets must be unique: {dst_n!r} is the target of both "
                f"{seen_dst[dst_n]!r} and {src_n!r} (pull could not tell them apart)"
            )
        if dst_n == home:
            raise ConfigError(
                f"mapping target {dst_n!r} equals remote.home (pull could not tell them apart)"
            )
        mappings[src_n] = dst_n
        seen_dst[dst_n] = src_n

    return Config(host=host, remote_home=home, mappings=mappings, **flags)


def dumps(cfg: Config) -> str:
    """Serialize a Config to TOML. json.dumps escaping is a subset of TOML
    basic-string escaping, so values survive quotes, backslashes, unicode."""
    q = json.dumps
    lines = [
        "[remote]",
        f"host = {q(cfg.host)}",
        f"home = {q(cfg.remote_home)}",
        "",
        "[sync]",
        f"include_history = {_toml_bool(cfg.include_history)}",
        f"include_agents = {_toml_bool(cfg.include_agents)}",
        f"include_skills = {_toml_bool(cfg.include_skills)}",
        "",
        "[mappings]",
        "# Projects whose relative path differs between the machines, e.g.:",
        f'# "{Path.home()}/work/webshop" = "{cfg.remote_home}/projects/webshop"',
    ]
    lines.extend(f"{q(src)} = {q(dst)}" for src, dst in cfg.mappings.items())
    return "\n".join(lines) + "\n"


def save(cfg: Config, path: str | Path | None = None) -> Path:
    path = Path(path) if path is not None else default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(cfg), encoding="utf-8")
    return path


def _normalize_abs(path: str, what: str) -> str:
    if not path.startswith("/"):
        raise ConfigError(f"{what} must be an absolute path, got {path!r}")
    normalized = path.rstrip("/")
    if not normalized:
        raise ConfigError(f"{what} must not be the filesystem root")
    return normalized


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"
