from pathlib import Path

import pytest

from claude_hop import config
from claude_hop.config import Config, ConfigError, Remote, resolve_remote

MULTI = """\
[remotes.mac]
host = "mac.local"
home = "/Users/alice"

[remotes.mac.mappings]
"/home/alice/work/webshop" = "/Users/alice/projects/webshop"

[remotes.thinkpad]
host = "192.168.1.50"
home = "/home/vas"

[sync]
include_history = true

[mappings]
"/home/alice/work/blog" = "/srv/blog"
"""

LEGACY = """\
[remote]
host = "mac.local"
home = "/Users/alice"

[sync]
include_history = true

[mappings]
"/home/alice/work/webshop" = "/Users/alice/projects/webshop"
"""


def write(tmp_path: Path, text: str) -> Path:
    p = tmp_path / "config.toml"
    p.write_text(text, encoding="utf-8")
    return p


# ------------------------------------------------------------- new format


def test_multi_remote_parse(tmp_path):
    cfg = config.load(write(tmp_path, MULTI))
    assert not cfg.legacy
    assert list(cfg.remotes) == ["mac", "thinkpad"]
    assert cfg.remotes["mac"].host == "mac.local"
    assert cfg.remotes["mac"].home == "/Users/alice"
    assert cfg.remotes["thinkpad"].host == "192.168.1.50"
    assert cfg.include_history is True
    assert cfg.mappings == {"/home/alice/work/blog": "/srv/blog"}


def test_merged_mappings_global_plus_per_remote(tmp_path):
    cfg = config.load(write(tmp_path, MULTI))
    assert cfg.merged_mappings("mac") == {
        "/home/alice/work/blog": "/srv/blog",
        "/home/alice/work/webshop": "/Users/alice/projects/webshop",
    }
    assert cfg.merged_mappings("thinkpad") == {"/home/alice/work/blog": "/srv/blog"}


def test_per_remote_mapping_overrides_global(tmp_path):
    text = """\
[remotes.mac]
host = "m"
home = "/Users/a"

[remotes.mac.mappings]
"/home/a/x" = "/Users/a/mac-specific"

[remotes.pad]
host = "p"
home = "/home/b"

[mappings]
"/home/a/x" = "/global/x"
"""
    cfg = config.load(write(tmp_path, text))
    assert cfg.merged_mappings("mac")["/home/a/x"] == "/Users/a/mac-specific"
    assert cfg.merged_mappings("pad")["/home/a/x"] == "/global/x"


def test_empty_remotes_table_is_valid(tmp_path):
    cfg = config.load(write(tmp_path, "[remotes]\n"))
    assert cfg.remotes == {}


def test_invalid_remote_name_rejected(tmp_path):
    text = '[remotes."bad name"]\nhost = "m"\nhome = "/u/a"\n'
    with pytest.raises(ConfigError, match="invalid remote name"):
        config.load(write(tmp_path, text))


def test_duplicate_remote_names_rejected(tmp_path):
    text = '[remotes.mac]\nhost = "a"\nhome = "/u/a"\n[remotes.mac]\nhost = "b"\nhome = "/u/b"\n'
    with pytest.raises(ConfigError, match="invalid TOML"):
        config.load(write(tmp_path, text))


def test_remote_missing_home_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"remotes\.mac.*home"):
        config.load(write(tmp_path, '[remotes.mac]\nhost = "m"\n'))


def test_merged_duplicate_targets_rejected(tmp_path):
    text = """\
[remotes.mac]
host = "m"
home = "/Users/a"

[remotes.mac.mappings]
"/home/a/x" = "/Users/a/z"

[mappings]
"/home/a/y" = "/Users/a/z"
"""
    with pytest.raises(ConfigError, match=r"remote 'mac'.*unique"):
        config.load(write(tmp_path, text))


def test_mapping_target_equal_to_remote_home_rejected(tmp_path):
    text = '[remotes.mac]\nhost = "m"\nhome = "/u/a"\n[mappings]\n"/h/x" = "/u/a"\n'
    with pytest.raises(ConfigError, match="equals its home"):
        config.load(write(tmp_path, text))


def test_global_mapping_conflicting_with_one_of_two_remotes(tmp_path):
    # the conflict is per-remote: /u/a is pad's home, fine for mac
    text = """\
[remotes.mac]
host = "m"
home = "/Users/a"

[remotes.pad]
host = "p"
home = "/u/a"

[mappings]
"/h/x" = "/u/a"
"""
    with pytest.raises(ConfigError, match="remote 'pad'"):
        config.load(write(tmp_path, text))


# ---------------------------------------------------------------- legacy


def test_legacy_parses_as_default_remote(tmp_path):
    cfg = config.load(write(tmp_path, LEGACY))
    assert cfg.legacy
    assert list(cfg.remotes) == ["default"]
    r = cfg.remotes["default"]
    assert (r.host, r.home) == ("mac.local", "/Users/alice")
    assert cfg.include_history is True
    # old top-level [mappings] stays global
    assert cfg.merged_mappings("default") == {
        "/home/alice/work/webshop": "/Users/alice/projects/webshop"
    }


def test_both_formats_rejected(tmp_path):
    text = LEGACY + '\n[remotes.mac]\nhost = "m"\nhome = "/u/a"\n'
    with pytest.raises(ConfigError, match="both"):
        config.load(write(tmp_path, text))


def test_neither_format_rejected(tmp_path):
    with pytest.raises(ConfigError, match=r"\[remotes\]"):
        config.load(write(tmp_path, "[sync]\ninclude_history = true\n"))


def test_migrate_round_trip(tmp_path):
    path = write(tmp_path, LEGACY)
    before = config.load(path)
    backup = config.migrate(path)

    assert backup == tmp_path / "config.toml.bak"
    assert backup.read_text(encoding="utf-8") == LEGACY
    after = config.load(path)
    assert not after.legacy
    assert after.remotes == before.remotes
    assert after.mappings == before.mappings
    assert after.include_history == before.include_history


def test_migrate_refuses_existing_backup(tmp_path):
    path = write(tmp_path, LEGACY)
    (tmp_path / "config.toml.bak").write_text("precious old backup", encoding="utf-8")
    with pytest.raises(ConfigError, match="move it aside"):
        config.migrate(path)
    # nothing was touched
    assert (tmp_path / "config.toml.bak").read_text(encoding="utf-8") == "precious old backup"
    assert config.load(path).legacy


def test_migrate_refuses_new_format(tmp_path):
    path = write(tmp_path, MULTI)
    with pytest.raises(ConfigError, match="already"):
        config.migrate(path)


# ------------------------------------------------------------- resolution


def two_remotes() -> Config:
    return Config(
        remotes={
            "mac": Remote(name="mac", host="m", home="/Users/a"),
            "thinkpad": Remote(name="thinkpad", host="t", home="/home/b"),
        }
    )


def test_resolve_explicit_name():
    assert resolve_remote(two_remotes(), "thinkpad").host == "t"


def test_resolve_sole_remote_implicitly():
    cfg = Config(remotes={"mac": Remote(name="mac", host="m", home="/Users/a")})
    assert resolve_remote(cfg, None).name == "mac"


def test_resolve_multiple_without_name_errors():
    with pytest.raises(ConfigError, match=r"Multiple remotes.*mac, thinkpad.*--all"):
        resolve_remote(two_remotes(), None)


def test_resolve_unknown_name_lists_available():
    with pytest.raises(ConfigError, match=r"unknown remote 'nas'.*mac, thinkpad"):
        resolve_remote(two_remotes(), "nas")


def test_resolve_no_remotes():
    with pytest.raises(ConfigError, match="no remotes configured"):
        resolve_remote(Config(), None)


# ------------------------------------------------------------ round trips


def test_dumps_load_round_trip(tmp_path):
    cfg = Config(
        remotes={
            "mac": Remote(
                name="mac",
                host="mac.local",
                home="/Users/alice",
                mappings={"/home/alice/we\"ird — path": "/Users/alice/wéird"},
            ),
            "pad": Remote(name="pad", host="", home="/mnt/pad-home"),
        },
        include_history=True,
        mappings={"/home/alice/work/blog": "/srv/blog"},
    )
    path = config.save(cfg, tmp_path / "sub" / "config.toml")
    assert config.load(path) == cfg


def test_zero_remote_round_trip(tmp_path):
    cfg = Config(include_skills=True)
    path = config.save(cfg, tmp_path / "config.toml")
    assert config.load(path) == cfg


def test_default_config_path_honours_xdg(monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "/custom/xdg")
    assert config.default_config_path() == Path("/custom/xdg/claude-hop/config.toml")


def test_default_config_path_fallback(monkeypatch):
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    assert config.default_config_path() == Path.home() / ".config/claude-hop/config.toml"
