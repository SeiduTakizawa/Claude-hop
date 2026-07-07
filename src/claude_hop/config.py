"""Config schema, XDG path resolution, and TOML load/save.

Multi-remote: named ``[remotes.<name>]`` tables, each with host/home and an
optional ``[remotes.<name>.mappings]`` table merged over the global
``[mappings]`` (per-remote wins). ``host = ""`` selects local-path mode,
where ``home`` is a directory on this machine.

The legacy single-``[remote]`` format still loads, as a remote named
"default", with ``cfg.legacy`` set so the CLI can hint at
``claude-hop remotes migrate``. Migration is never automatic.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_SYNC_FLAGS = ("include_history", "include_agents", "include_skills")
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]*$")


class ConfigError(Exception):
    """Invalid, missing, or unparseable configuration."""


def default_config_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "claude-hop" / "config.toml"


@dataclass
class Remote:
    name: str
    host: str  # SSH alias or hostname; "" = local-path mode
    home: str
    mappings: dict[str, str] = field(default_factory=dict)  # per-remote only


@dataclass
class Config:
    remotes: dict[str, Remote] = field(default_factory=dict)
    include_history: bool = False
    include_agents: bool = False
    include_skills: bool = False
    mappings: dict[str, str] = field(default_factory=dict)  # global
    legacy: bool = False  # parsed from the old single-[remote] format

    def merged_mappings(self, name: str) -> dict[str, str]:
        """Global mappings overlaid with the remote's own; per-remote wins."""
        merged = dict(self.mappings)
        merged.update(self.remotes[name].mappings)
        return merged


def resolve_remote(cfg: Config, name: str | None) -> Remote:
    """Pick the remote to operate on. Explicit name wins; a sole remote is
    used silently; anything else is an error that names the options."""
    if not cfg.remotes:
        raise ConfigError("no remotes configured — run: claude-hop init")
    if name is None:
        if len(cfg.remotes) == 1:
            return next(iter(cfg.remotes.values()))
        names = ", ".join(cfg.remotes)
        raise ConfigError(f"Multiple remotes configured ({names}). Specify one, or use --all.")
    if name not in cfg.remotes:
        names = ", ".join(cfg.remotes)
        raise ConfigError(f"unknown remote {name!r} — available: {names}")
    return cfg.remotes[name]


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
    legacy_table = data.get("remote")
    remotes_table = data.get("remotes")
    if legacy_table is not None and remotes_table is not None:
        raise ConfigError("config has both [remote] and [remotes] — keep only [remotes]")
    if legacy_table is None and remotes_table is None:
        raise ConfigError("missing [remotes] table")

    flags = _parse_sync(data)
    global_mappings = _parse_mappings(data.get("mappings", {}), where="[mappings]")

    remotes: dict[str, Remote] = {}
    legacy = False
    if legacy_table is not None:
        legacy = True
        remotes["default"] = _parse_remote("default", legacy_table, where="[remote]")
    else:
        if not isinstance(remotes_table, dict):
            raise ConfigError("[remotes] must be a table")
        for name, table in remotes_table.items():
            if not _NAME_RE.match(name):
                raise ConfigError(
                    f"invalid remote name {name!r} — use letters, digits, '-' and '_', "
                    "starting with a letter or digit"
                )
            remotes[name] = _parse_remote(name, table, where=f"[remotes.{name}]")

    cfg = Config(remotes=remotes, mappings=global_mappings, legacy=legacy, **flags)
    for name in remotes:
        _validate_merged(cfg, name)
    return cfg


def _parse_remote(name: str, table: object, *, where: str) -> Remote:
    if not isinstance(table, dict):
        raise ConfigError(f"{where} must be a table")
    host = table.get("host", "")
    if not isinstance(host, str):
        raise ConfigError(f"{where}.host must be a string")
    home = table.get("home")
    if not isinstance(home, str) or not home:
        raise ConfigError(f"{where}.home is required")
    home = _normalize_abs(home, f"{where}.home")
    mappings = _parse_mappings(table.get("mappings", {}), where=f"{where}.mappings")
    return Remote(name=name, host=host, home=home, mappings=mappings)


def _parse_sync(data: dict) -> dict[str, bool]:
    sync = data.get("sync", {})
    if not isinstance(sync, dict):
        raise ConfigError("[sync] must be a table")
    flags = {}
    for key in _SYNC_FLAGS:
        value = sync.get(key, False)
        if not isinstance(value, bool):
            raise ConfigError(f"sync.{key} must be true or false")
        flags[key] = value
    return flags


def _parse_mappings(raw: object, *, where: str) -> dict[str, str]:
    if not isinstance(raw, dict):
        raise ConfigError(f"{where} must be a table")
    mappings: dict[str, str] = {}
    for src, dst in raw.items():
        if not isinstance(dst, str):
            raise ConfigError(f"{where}: mapping for {src!r} must be a string path")
        src_n = _normalize_abs(src, f"{where} source {src!r}")
        dst_n = _normalize_abs(dst, f"{where} target {dst!r}")
        if src_n in mappings:
            raise ConfigError(f"{where}: duplicate mapping source {src_n!r}")
        mappings[src_n] = dst_n
    return mappings


def _validate_merged(cfg: Config, name: str) -> None:
    """The mappings a remote actually uses must be invertible for pull."""
    remote = cfg.remotes[name]
    seen_dst: dict[str, str] = {}
    for src, dst in cfg.merged_mappings(name).items():
        if dst in seen_dst:
            raise ConfigError(
                f"remote {name!r}: mapping targets must be unique — {dst!r} is the target "
                f"of both {seen_dst[dst]!r} and {src!r} (pull could not tell them apart)"
            )
        if dst == remote.home:
            raise ConfigError(
                f"remote {name!r}: mapping target {dst!r} equals its home "
                "(pull could not tell them apart)"
            )
        seen_dst[dst] = src


def dumps(cfg: Config) -> str:
    """Serialize to the multi-remote TOML format. json.dumps escaping is a
    subset of TOML basic-string escaping, so values survive quotes/unicode."""
    q = json.dumps
    lines: list[str] = ["[remotes]"]
    for r in cfg.remotes.values():
        lines += ["", f"[remotes.{r.name}]", f"host = {q(r.host)}", f"home = {q(r.home)}"]
        if r.mappings:
            lines += ["", f"[remotes.{r.name}.mappings]"]
            lines += [f"{q(src)} = {q(dst)}" for src, dst in r.mappings.items()]
    lines += [
        "",
        "[sync]",
        f"include_history = {_toml_bool(cfg.include_history)}",
        f"include_agents = {_toml_bool(cfg.include_agents)}",
        f"include_skills = {_toml_bool(cfg.include_skills)}",
        "",
        "[mappings]",
        "# Global mappings, applied to every remote; entries under",
        "# [remotes.<name>.mappings] override these for that remote.",
    ]
    lines.extend(f"{q(src)} = {q(dst)}" for src, dst in cfg.mappings.items())
    return "\n".join(lines) + "\n"


def save(cfg: Config, path: str | Path | None = None) -> Path:
    path = Path(path) if path is not None else default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dumps(cfg), encoding="utf-8")
    return path


def migrate(path: str | Path | None = None) -> Path:
    """Rewrite a legacy single-[remote] config in the multi-remote format.
    Returns the backup path; the original is kept at ``<name>.bak``."""
    path = Path(path) if path is not None else default_config_path()
    cfg = load(path)
    if not cfg.legacy:
        raise ConfigError(f"{path} is already in the multi-remote format")
    backup = path.with_name(path.name + ".bak")
    if backup.exists():
        raise ConfigError(f"{backup} already exists — move it aside first")
    shutil.copy2(path, backup)
    cfg.legacy = False
    save(cfg, path)
    return backup


def _normalize_abs(path: str, what: str) -> str:
    if not path.startswith("/"):
        raise ConfigError(f"{what} must be an absolute path, got {path!r}")
    normalized = path.rstrip("/")
    if not normalized:
        raise ConfigError(f"{what} must not be the filesystem root")
    return normalized


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"
