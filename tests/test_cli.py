import pytest
from typer.testing import CliRunner

from claude_hop import config as config_mod
from claude_hop.cli import app
from claude_hop.config import Config, Remote
from claude_hop.remap import encode_path
from conftest import local_cfg, make_session, requires_rsync, session_cwd

runner = CliRunner()


@pytest.fixture
def env(homes, monkeypatch, tmp_path):
    """Wire CLI env hooks to the fake homes and write a local-mode config."""
    local, remote = homes
    cfg_path = tmp_path / "config.toml"
    config_mod.save(local_cfg(remote), cfg_path)
    monkeypatch.setenv("CLAUDE_HOP_CONFIG", str(cfg_path))
    monkeypatch.setenv("CLAUDE_HOP_HOME", str(local))
    monkeypatch.setenv("COLUMNS", "400")
    return local, remote, cfg_path


def test_version():
    result = runner.invoke(app, ["--version"])
    assert result.exit_code == 0
    assert "claude-hop" in result.output


def test_status_lists_projects_and_mapping(env, not_running):
    local, remote, _ = env
    make_session(local, "work/webshop")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert encode_path(f"{local}/work/webshop") in result.output
    assert encode_path(f"{remote}/work/webshop") in result.output


def test_status_without_config(homes, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_HOP_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("CLAUDE_HOP_HOME", str(homes[0]))
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "claude-hop init" in result.output


@requires_rsync
def test_push_dry_run(env, not_running):
    local, remote, _ = env
    make_session(local, "work/webshop")
    result = runner.invoke(app, ["push", "--dry-run"])
    assert result.exit_code == 0
    assert "dry run" in result.output
    assert not (remote / ".claude").exists()


@requires_rsync
def test_push_verbose_lists_skipped_files(env, not_running):
    import os

    local, remote, _ = env
    f = make_session(local, "work/webshop")
    os.utime(f, (1_600_000_000, 1_600_000_000))
    target = remote / ".claude" / "projects" / encode_path(f"{remote}/work/webshop")
    target.mkdir(parents=True)
    newer = target / "s1.jsonl"
    newer.write_text("remote newer\n", encoding="utf-8")
    os.utime(newer, (1_700_000_000, 1_700_000_000))

    result = runner.invoke(app, ["push", "--verbose"])
    assert result.exit_code == 0, result.output
    assert "skipped" in result.output
    assert "s1.jsonl" in result.output


@requires_rsync
def test_push_then_pull(env, not_running):
    local, remote, _ = env
    make_session(local, "work/webshop")
    result = runner.invoke(app, ["push"])
    assert result.exit_code == 0
    assert (
        remote / ".claude" / "projects" / encode_path(f"{remote}/work/webshop") / "s1.jsonl"
    ).exists()

    make_session(remote, "other/beta")
    result = runner.invoke(app, ["pull", "--yes"])
    assert result.exit_code == 0
    assert (
        local / ".claude" / "projects" / encode_path(f"{local}/other/beta") / "s1.jsonl"
    ).exists()


@requires_rsync
def test_push_single_project_cli(env, not_running):
    local, remote, _ = env
    make_session(local, "work/webshop")
    make_session(local, "work/blog")
    result = runner.invoke(app, ["push", f"{local}/work/webshop"])
    assert result.exit_code == 0, result.output
    remote_projects = remote / ".claude" / "projects"
    assert (remote_projects / encode_path(f"{remote}/work/webshop")).is_dir()
    assert not (remote_projects / encode_path(f"{remote}/work/blog")).exists()


@requires_rsync
def test_push_unknown_project_cli(env, not_running):
    local, _, _ = env
    make_session(local, "work/webshop")
    result = runner.invoke(app, ["push", f"{local}/work/nope"])
    assert result.exit_code == 1
    assert "unknown local project" in result.output


@requires_rsync
def test_diff_single_project_cli(env, not_running):
    local, remote, _ = env
    make_session(local, "work/webshop")
    make_session(local, "work/blog")
    result = runner.invoke(app, ["diff", f"{local}/work/webshop"])
    assert result.exit_code == 0
    assert encode_path(f"{remote}/work/webshop") in result.output
    assert encode_path(f"{remote}/work/blog") not in result.output


def test_push_refuses_while_running(env, monkeypatch):
    local, _, _ = env
    make_session(local, "work/webshop")
    from claude_hop import sync

    monkeypatch.setattr(sync, "claude_running", lambda: True)
    result = runner.invoke(app, ["push"])
    assert result.exit_code == 1
    assert "--force" in result.output


@requires_rsync
def test_diff_shows_both_directions(env, not_running):
    local, remote, _ = env
    make_session(local, "work/webshop")
    result = runner.invoke(app, ["diff"])
    assert result.exit_code == 0
    assert "Outgoing" in result.output
    assert "Incoming" in result.output
    assert encode_path(f"{remote}/work/webshop") in result.output  # would push
    assert "no sessions on the remote" in result.output  # nothing to pull yet

    runner.invoke(app, ["push"])
    result = runner.invoke(app, ["diff"])
    assert result.exit_code == 0
    assert result.output.count("already in sync") == 2


def test_doctor_healthy(env, not_running, monkeypatch, tmp_path):
    import tempfile

    local, remote, _ = env
    make_session(local, "work/webshop")
    (remote / ".claude" / "projects").mkdir(parents=True)
    fake_tmp = tmp_path / "faketmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_tmp))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "ready to sync" in result.output


def test_doctor_fails_without_config(homes, monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_HOP_CONFIG", str(tmp_path / "missing.toml"))
    monkeypatch.setenv("CLAUDE_HOP_HOME", str(homes[0]))
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "check(s) failed" in result.output


@pytest.fixture
def multi_env(homes, monkeypatch, tmp_path):
    """Two local-path remotes ("mac" and "pad") with distinct fake homes."""
    local, mac_home = homes
    pad_home = tmp_path / "Users" / "second"
    pad_home.mkdir(parents=True)
    cfg = Config(
        remotes={
            "mac": Remote(name="mac", host="", home=str(mac_home)),
            "pad": Remote(name="pad", host="", home=str(pad_home)),
        }
    )
    cfg_path = tmp_path / "config.toml"
    config_mod.save(cfg, cfg_path)
    monkeypatch.setenv("CLAUDE_HOP_CONFIG", str(cfg_path))
    monkeypatch.setenv("CLAUDE_HOP_HOME", str(local))
    monkeypatch.setenv("COLUMNS", "400")
    return local, mac_home, pad_home, cfg_path


@pytest.fixture
def mac_env(homes, monkeypatch, tmp_path):
    """A sole remote named 'mac' (for the typo did-you-mean tests)."""
    local, mac_home = homes
    cfg_path = tmp_path / "config.toml"
    config_mod.save(local_cfg(mac_home, name="mac"), cfg_path)
    monkeypatch.setenv("CLAUDE_HOP_CONFIG", str(cfg_path))
    monkeypatch.setenv("CLAUDE_HOP_HOME", str(local))
    monkeypatch.setenv("COLUMNS", "400")
    return local, mac_home


def test_push_multiple_remotes_requires_name(multi_env, not_running):
    local, *_ = multi_env
    make_session(local, "work/webshop")
    result = runner.invoke(app, ["push"])
    assert result.exit_code == 1
    assert "Multiple remotes configured (mac, pad)" in result.output


@requires_rsync
def test_push_explicit_remote_name(multi_env, not_running):
    local, mac_home, pad_home, _ = multi_env
    make_session(local, "work/webshop")
    result = runner.invoke(app, ["push", "mac"])
    assert result.exit_code == 0, result.output
    assert (mac_home / ".claude" / "projects").is_dir()
    assert not (pad_home / ".claude").exists()


def test_push_remote_typo_is_hard_error(mac_env, not_running):
    local, mac_home = mac_env
    make_session(local, "work/webshop")
    result = runner.invoke(app, ["push", "mca"])
    assert result.exit_code == 1
    assert "no project matching 'mca'" in result.output
    assert "did you mean remote 'mac'" in result.output
    # nothing was transferred
    assert not (mac_home / ".claude").exists()


@requires_rsync
def test_push_remote_name_shadowing_directory_notice(multi_env, not_running, monkeypatch):
    local, mac_home, _, _ = multi_env
    make_session(local, "work/webshop")
    shadow = local / "cwd-play"
    (shadow / "mac").mkdir(parents=True)
    monkeypatch.chdir(shadow)
    result = runner.invoke(app, ["push", "mac"])
    assert result.exit_code == 0, result.output
    assert "treated as a remote name" in result.stderr
    assert "./mac" in result.stderr
    assert (mac_home / ".claude" / "projects").is_dir()


@requires_rsync
def test_push_all_syncs_every_remote(multi_env, not_running):
    local, mac_home, pad_home, _ = multi_env
    make_session(local, "work/webshop")
    result = runner.invoke(app, ["push", "--all"])
    assert result.exit_code == 0, result.output
    for home in (mac_home, pad_home):
        out = (
            home / ".claude" / "projects" / encode_path(f"{home}/work/webshop") / "s1.jsonl"
        )
        assert session_cwd(out) == f"{home}/work/webshop"


@requires_rsync
def test_push_all_continues_past_failure(multi_env, not_running, tmp_path):
    local, mac_home, _, cfg_path = multi_env
    make_session(local, "work/webshop")
    blockfile = tmp_path / "blockfile"
    blockfile.write_text("not a directory", encoding="utf-8")
    cfg = Config(
        remotes={
            "mac": Remote(name="mac", host="", home=str(mac_home)),
            "pad": Remote(name="pad", host="", home=str(blockfile)),
        }
    )
    config_mod.save(cfg, cfg_path)
    result = runner.invoke(app, ["push", "--all"])
    assert result.exit_code == 1
    assert "1 remote(s) failed" in result.output
    # the healthy remote still got the sessions
    assert (mac_home / ".claude" / "projects").is_dir()


def test_push_remote_and_all_conflict(multi_env, not_running):
    local, *_ = multi_env
    make_session(local, "work/webshop")
    result = runner.invoke(app, ["push", "mac", "--all"])
    assert result.exit_code == 1
    assert "not both" in result.output


@requires_rsync
def test_pull_all_merges_from_every_remote(multi_env, not_running):
    local, mac_home, pad_home, _ = multi_env
    make_session(mac_home, "work/frommac")
    make_session(pad_home, "work/frompad")
    result = runner.invoke(app, ["pull", "--all", "--yes"])
    assert result.exit_code == 0, result.output
    for rel in ("work/frommac", "work/frompad"):
        out = local / ".claude" / "projects" / encode_path(f"{local}/{rel}") / "s1.jsonl"
        assert session_cwd(out) == f"{local}/{rel}"


def test_status_with_remote_argument(multi_env, not_running):
    local, _, pad_home, _ = multi_env
    make_session(local, "work/webshop")
    result = runner.invoke(app, ["status", "pad"])
    assert result.exit_code == 0
    assert encode_path(f"{pad_home}/work/webshop") in result.output

    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "Multiple remotes" in result.output


def test_doctor_all_and_single_remote(multi_env, not_running, monkeypatch, tmp_path):
    import tempfile

    local, *_ = multi_env
    make_session(local, "work/webshop")
    fake_tmp = tmp_path / "faketmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_tmp))

    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 0, result.output
    assert "(mac)" in result.output and "(pad)" in result.output

    result = runner.invoke(app, ["doctor", "pad"])
    assert result.exit_code == 0, result.output
    assert "(mac)" not in result.output

    # unknown name: the same resolution error as push/pull/diff/status
    result = runner.invoke(app, ["doctor", "nope"])
    assert result.exit_code == 1
    assert "unknown remote 'nope'" in result.output
    assert "available: mac, pad" in result.output


@requires_rsync
def test_encoded_selector_via_double_dash(multi_env, not_running):
    local, mac_home, _, _ = multi_env
    make_session(local, "work/webshop")
    make_session(local, "work/blog")
    enc = encode_path(f"{local}/work/webshop")

    # a bare dash-prefixed selector is eaten by the option parser (exit 2)…
    result = runner.invoke(app, ["push", "mac", enc])
    assert result.exit_code == 2

    # …but the standard "--" separator passes it through as a selector
    result = runner.invoke(app, ["push", "mac", "--", enc])
    assert result.exit_code == 0, result.output
    projects = mac_home / ".claude" / "projects"
    assert (projects / encode_path(f"{mac_home}/work/webshop")).is_dir()
    assert not (projects / encode_path(f"{mac_home}/work/blog")).exists()


def test_upgrade_already_up_to_date(monkeypatch):
    from claude_hop import banner, upgrade

    monkeypatch.setattr(upgrade, "latest_version", lambda: banner.get_version())
    result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 0
    assert "already up to date" in result.output


def test_upgrade_refuses_dev_checkout(monkeypatch):
    from claude_hop import upgrade

    monkeypatch.setattr(upgrade, "latest_version", lambda: "999.0.0")
    result = runner.invoke(app, ["upgrade"])
    assert result.exit_code == 1
    assert "git pull" in result.output


def test_init_local_mode_writes_config(homes, monkeypatch, tmp_path):
    _, remote = homes
    cfg_path = tmp_path / "cfg" / "config.toml"
    monkeypatch.setenv("CLAUDE_HOP_CONFIG", str(cfg_path))
    # prompts: host (empty), local dir, remote name (accept default "local")
    result = runner.invoke(app, ["init"], input=f"\n{remote}\n\n")
    assert result.exit_code == 0, result.output
    cfg = config_mod.load(cfg_path)
    assert list(cfg.remotes) == ["local"]
    assert cfg.remotes["local"].host == ""
    assert cfg.remotes["local"].home == str(remote)


def test_legacy_config_prints_migrate_hint(homes, monkeypatch, tmp_path):
    local, remote = homes
    make_session(local, "work/webshop")
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(f'[remote]\nhost = ""\nhome = "{remote}"\n', encoding="utf-8")
    monkeypatch.setenv("CLAUDE_HOP_CONFIG", str(cfg_path))
    monkeypatch.setenv("CLAUDE_HOP_HOME", str(local))
    monkeypatch.setenv("COLUMNS", "300")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0, result.output
    # the hint goes to stderr so piped stdout stays clean
    assert "remotes migrate" not in result.stdout
    assert "remotes migrate" in result.stderr


def test_init_refuses_relative_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_HOP_CONFIG", str(tmp_path / "config.toml"))
    result = runner.invoke(app, ["init"], input="\nnot/absolute\n")
    assert result.exit_code == 1
    assert "absolute" in result.output
