from pathlib import Path

import pytest

from claude_hop import config
from claude_hop.config import Config, ConfigError

FULL = """\
[remote]
host = "mac.local"
home = "/Users/alice"

[sync]
include_history = true
include_agents = false
include_skills = true

[mappings]
"/home/alice/work/webshop" = "/Users/alice/projects/webshop"
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


def test_full_config(tmp_path):
    cfg = config.load(write(tmp_path, FULL))
    assert cfg == Config(
        host="mac.local",
        remote_home="/Users/alice",
        include_history=True,
        include_agents=False,
        include_skills=True,
        mappings={"/home/alice/work/webshop": "/Users/alice/projects/webshop"},
    )


def test_minimal_config_defaults(tmp_path):
    cfg = config.load(write(tmp_path, '[remote]\nhost = "mac"\nhome = "/Users/alice"\n'))
    assert cfg.include_history is cfg.include_agents is cfg.include_skills is False
    assert cfg.mappings == {}


def test_empty_host_allowed_for_local_mode(tmp_path):
    cfg = config.load(write(tmp_path, '[remote]\nhost = ""\nhome = "/mnt/fake-remote/home"\n'))
    assert cfg.host == ""


def test_remote_home_trailing_slash_normalized(tmp_path):
    cfg = config.load(write(tmp_path, '[remote]\nhost = "m"\nhome = "/Users/alice/"\n'))
    assert cfg.remote_home == "/Users/alice"


def test_missing_file():
    with pytest.raises(ConfigError, match="claude-hop init"):
        config.load("/nonexistent/config.toml")


def test_invalid_toml(tmp_path):
    with pytest.raises(ConfigError, match="invalid TOML"):
        config.load(write(tmp_path, "[remote\nhost="))


def test_missing_remote_table(tmp_path):
    with pytest.raises(ConfigError, match=r"\[remote\]"):
        config.load(write(tmp_path, '[sync]\ninclude_history = true\n'))


def test_missing_home(tmp_path):
    with pytest.raises(ConfigError, match=r"remote\.home"):
        config.load(write(tmp_path, '[remote]\nhost = "mac"\n'))


def test_relative_home_rejected(tmp_path):
    with pytest.raises(ConfigError, match="absolute"):
        config.load(write(tmp_path, '[remote]\nhost = "mac"\nhome = "Users/alice"\n'))


def test_root_home_rejected(tmp_path):
    with pytest.raises(ConfigError, match="root"):
        config.load(write(tmp_path, '[remote]\nhost = "mac"\nhome = "/"\n'))


def test_non_bool_sync_flag_rejected(tmp_path):
    text = '[remote]\nhost = "m"\nhome = "/u/a"\n[sync]\ninclude_history = "yes"\n'
    with pytest.raises(ConfigError, match="include_history"):
        config.load(write(tmp_path, text))


def test_relative_mapping_rejected(tmp_path):
    text = '[remote]\nhost = "m"\nhome = "/u/a"\n[mappings]\n"work/x" = "/u/a/x"\n'
    with pytest.raises(ConfigError, match="absolute"):
        config.load(write(tmp_path, text))


def test_duplicate_mapping_targets_rejected(tmp_path):
    text = (
        '[remote]\nhost = "m"\nhome = "/u/a"\n[mappings]\n'
        '"/h/a/x" = "/u/a/z"\n"/h/a/y" = "/u/a/z"\n'
    )
    with pytest.raises(ConfigError, match="unique"):
        config.load(write(tmp_path, text))


def test_mapping_sources_colliding_after_normalization_rejected(tmp_path):
    text = (
        '[remote]\nhost = "m"\nhome = "/u/a"\n[mappings]\n'
        '"/h/a/x" = "/u/a/x"\n"/h/a/x/" = "/u/a/y"\n'
    )
    with pytest.raises(ConfigError, match="duplicate"):
        config.load(write(tmp_path, text))


def test_mapping_target_equal_to_remote_home_rejected(tmp_path):
    text = '[remote]\nhost = "m"\nhome = "/u/a"\n[mappings]\n"/h/a/x" = "/u/a"\n'
    with pytest.raises(ConfigError, match=r"remote\.home"):
        config.load(write(tmp_path, text))


def test_dumps_load_round_trip(tmp_path):
    cfg = Config(
        host="mac.local",
        remote_home="/Users/alice",
        include_history=True,
        mappings={
            "/home/alice/work/webshop": "/Users/alice/projects/webshop",
            '/home/alice/we"ird — path': "/Users/alice/wéird",
        },
    )
    path = config.save(cfg, tmp_path / "sub" / "config.toml")
    assert config.load(path) == cfg


def test_default_config_path_honours_xdg(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/xdg")
    assert config.default_config_path() == Path("/custom/xdg/claude-hop/config.toml")


def test_default_config_path_fallback(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config.default_config_path() == Path.home() / ".config/claude-hop/config.toml"
