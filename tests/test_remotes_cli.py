"""The `claude-hop remotes` command group and init-with-existing-config."""

import pytest
from typer.testing import CliRunner

from claude_hop import config as config_mod
from claude_hop.cli import app
from claude_hop.config import Config, Remote

runner = CliRunner()

LEGACY_TEXT = '[remote]\nhost = ""\nhome = "{home}"\n'


@pytest.fixture
def cfg_env(monkeypatch, tmp_path):
    """Writes a config with the given remotes and wires the env; returns paths."""
    cfg_path = tmp_path / "config.toml"
    monkeypatch.setenv("CLAUDE_HOP_CONFIG", str(cfg_path))
    monkeypatch.setenv("COLUMNS", "300")

    def write(remotes: dict[str, str]):
        cfg = Config(
            remotes={
                name: Remote(name=name, host="", home=str(home))
                for name, home in remotes.items()
            }
        )
        config_mod.save(cfg, cfg_path)
        return cfg_path

    return write, cfg_path, tmp_path


def homes_for(tmp_path, *names):
    out = []
    for n in names:
        d = tmp_path / f"home-{n}"
        d.mkdir(exist_ok=True)
        out.append(d)
    return out


# ------------------------------------------------------------------- list


def test_list_zero_remotes(cfg_env):
    write, *_ = cfg_env
    write({})
    result = runner.invoke(app, ["remotes"])
    assert result.exit_code == 0, result.output
    assert "no remotes configured" in result.output


def test_list_renders_remotes(cfg_env):
    write, _, tmp_path = cfg_env
    mac, pad = homes_for(tmp_path, "mac", "pad")
    write({"mac": mac, "pad": pad})
    result = runner.invoke(app, ["remotes"])
    assert result.exit_code == 0
    assert "mac" in result.output and "pad" in result.output
    assert str(mac) in result.output
    assert "—" in result.output  # status column not probed by default


def test_list_explicit_subcommand_and_check_local(cfg_env):
    write, _, tmp_path = cfg_env
    (mac,) = homes_for(tmp_path, "mac")
    write({"mac": mac})
    result = runner.invoke(app, ["remotes", "list", "--check"])
    assert result.exit_code == 0, result.output
    assert "local" in result.output


# -------------------------------------------------------------------- add


def test_add_appends_remote(cfg_env):
    write, cfg_path, tmp_path = cfg_env
    mac, pad = homes_for(tmp_path, "mac", "pad")
    write({"mac": mac})
    result = runner.invoke(app, ["remotes", "add", "pad"], input=f"\n{pad}\n")
    assert result.exit_code == 0, result.output
    cfg = config_mod.load(cfg_path)
    assert list(cfg.remotes) == ["mac", "pad"]
    assert cfg.remotes["pad"].home == str(pad)


def test_add_refuses_duplicate_name(cfg_env):
    write, _, tmp_path = cfg_env
    (mac,) = homes_for(tmp_path, "mac")
    write({"mac": mac})
    result = runner.invoke(app, ["remotes", "add", "mac"], input="\n/tmp/x\n")
    assert result.exit_code == 1
    assert "already exists" in result.output


def test_add_refuses_invalid_name(cfg_env):
    write, _, tmp_path = cfg_env
    (mac,) = homes_for(tmp_path, "mac")
    write({"mac": mac})
    result = runner.invoke(app, ["remotes", "add", "bad name"])
    assert result.exit_code == 1
    assert "invalid remote name" in result.output


def test_add_refuses_legacy_config(cfg_env):
    _, cfg_path, tmp_path = cfg_env
    cfg_path.write_text(LEGACY_TEXT.format(home=tmp_path), encoding="utf-8")
    result = runner.invoke(app, ["remotes", "add", "pad"])
    assert result.exit_code == 1
    assert "remotes migrate" in result.output


# ----------------------------------------------------------------- remove


def test_remove_with_yes(cfg_env):
    write, cfg_path, tmp_path = cfg_env
    mac, pad = homes_for(tmp_path, "mac", "pad")
    write({"mac": mac, "pad": pad})
    result = runner.invoke(app, ["remotes", "remove", "pad", "--yes"])
    assert result.exit_code == 0, result.output
    assert list(config_mod.load(cfg_path).remotes) == ["mac"]


def test_remove_declined_leaves_config(cfg_env):
    write, cfg_path, tmp_path = cfg_env
    mac, pad = homes_for(tmp_path, "mac", "pad")
    write({"mac": mac, "pad": pad})
    result = runner.invoke(app, ["remotes", "remove", "pad"], input="n\n")
    assert result.exit_code == 1  # typer abort
    assert list(config_mod.load(cfg_path).remotes) == ["mac", "pad"]


def test_remove_unknown_lists_available(cfg_env):
    write, _, tmp_path = cfg_env
    (mac,) = homes_for(tmp_path, "mac")
    write({"mac": mac})
    result = runner.invoke(app, ["remotes", "remove", "nas", "--yes"])
    assert result.exit_code == 1
    assert "unknown remote 'nas'" in result.output
    assert "mac" in result.output


def test_remove_last_remote_warns(cfg_env):
    write, cfg_path, tmp_path = cfg_env
    (mac,) = homes_for(tmp_path, "mac")
    write({"mac": mac})
    result = runner.invoke(app, ["remotes", "remove", "mac", "--yes"])
    assert result.exit_code == 0, result.output
    assert "no remotes left" in result.output
    assert config_mod.load(cfg_path).remotes == {}


# ----------------------------------------------------------------- rename


def test_rename_preserves_order_and_mappings(cfg_env, tmp_path):
    _, cfg_path, _ = cfg_env
    mac, pad = homes_for(tmp_path, "mac", "pad")
    cfg = Config(
        remotes={
            "mac": Remote(
                name="mac", host="", home=str(mac), mappings={"/a/b": "/c/d"}
            ),
            "pad": Remote(name="pad", host="", home=str(pad)),
        }
    )
    config_mod.save(cfg, cfg_path)
    result = runner.invoke(app, ["remotes", "rename", "mac", "laptop"])
    assert result.exit_code == 0, result.output
    loaded = config_mod.load(cfg_path)
    assert list(loaded.remotes) == ["laptop", "pad"]
    assert loaded.remotes["laptop"].name == "laptop"
    assert loaded.remotes["laptop"].mappings == {"/a/b": "/c/d"}


def test_rename_refuses_existing_target(cfg_env):
    write, _, tmp_path = cfg_env
    mac, pad = homes_for(tmp_path, "mac", "pad")
    write({"mac": mac, "pad": pad})
    result = runner.invoke(app, ["remotes", "rename", "mac", "pad"])
    assert result.exit_code == 1
    assert "already exists" in result.output


# ---------------------------------------------------------------- migrate


def test_migrate_command(cfg_env):
    _, cfg_path, tmp_path = cfg_env
    original = LEGACY_TEXT.format(home=tmp_path)
    cfg_path.write_text(original, encoding="utf-8")
    result = runner.invoke(app, ["remotes", "migrate"])
    assert result.exit_code == 0, result.output
    backup = cfg_path.with_name("config.toml.bak")
    assert backup.read_text(encoding="utf-8") == original
    cfg = config_mod.load(cfg_path)
    assert not cfg.legacy
    assert list(cfg.remotes) == ["default"]

    # running it again is a clear error
    result = runner.invoke(app, ["remotes", "migrate"])
    assert result.exit_code == 1
    assert "already" in result.output


# ---------------------------------------------------- init with a config


def test_init_existing_offers_add(cfg_env):
    write, cfg_path, tmp_path = cfg_env
    mac, pad = homes_for(tmp_path, "mac", "pad")
    write({"mac": mac})
    # confirm add (default yes), name, empty host, local dir
    result = runner.invoke(app, ["init"], input=f"\npad\n\n{pad}\n")
    assert result.exit_code == 0, result.output
    cfg = config_mod.load(cfg_path)
    assert list(cfg.remotes) == ["mac", "pad"]


def test_init_legacy_offers_inline_migration(cfg_env):
    _, cfg_path, tmp_path = cfg_env
    (pad,) = homes_for(tmp_path, "pad")
    cfg_path.write_text(LEGACY_TEXT.format(home=tmp_path), encoding="utf-8")
    # migrate? (default yes), add? (default yes), name, empty host, local dir
    result = runner.invoke(app, ["init"], input=f"\n\npad\n\n{pad}\n")
    assert result.exit_code == 0, result.output
    assert "migrated" in result.output
    assert cfg_path.with_name("config.toml.bak").exists()
    cfg = config_mod.load(cfg_path)
    assert not cfg.legacy
    assert list(cfg.remotes) == ["default", "pad"]
    assert cfg.remotes["pad"].home == str(pad)


def test_init_legacy_migration_declined_aborts_cleanly(cfg_env):
    _, cfg_path, tmp_path = cfg_env
    original = LEGACY_TEXT.format(home=tmp_path)
    cfg_path.write_text(original, encoding="utf-8")
    result = runner.invoke(app, ["init"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "nothing changed" in result.output
    assert cfg_path.read_text(encoding="utf-8") == original
    assert not cfg_path.with_name("config.toml.bak").exists()


def test_init_existing_declined_changes_nothing(cfg_env):
    write, cfg_path, tmp_path = cfg_env
    (mac,) = homes_for(tmp_path, "mac")
    write({"mac": mac})
    before = cfg_path.read_text(encoding="utf-8")
    result = runner.invoke(app, ["init"], input="n\n")
    assert result.exit_code == 0, result.output
    assert "nothing changed" in result.output
    assert cfg_path.read_text(encoding="utf-8") == before