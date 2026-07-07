import pytest
from typer.testing import CliRunner

from claude_hop import config as config_mod
from claude_hop.cli import app
from claude_hop.config import Config
from claude_hop.remap import encode_path
from conftest import make_session, requires_rsync

runner = CliRunner()


@pytest.fixture
def env(homes, monkeypatch, tmp_path):
    """Wire CLI env hooks to the fake homes and write a local-mode config."""
    local, remote = homes
    cfg_path = tmp_path / "config.toml"
    config_mod.save(Config(host="", remote_home=str(remote)), cfg_path)
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


def test_init_local_mode_writes_config(homes, monkeypatch, tmp_path):
    _, remote = homes
    cfg_path = tmp_path / "cfg" / "config.toml"
    monkeypatch.setenv("CLAUDE_HOP_CONFIG", str(cfg_path))
    result = runner.invoke(app, ["init"], input=f"\n{remote}\n")
    assert result.exit_code == 0, result.output
    cfg = config_mod.load(cfg_path)
    assert cfg.host == ""
    assert cfg.remote_home == str(remote)


def test_init_refuses_relative_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CLAUDE_HOP_CONFIG", str(tmp_path / "config.toml"))
    result = runner.invoke(app, ["init"], input="\nnot/absolute\n")
    assert result.exit_code == 1
    assert "absolute" in result.output
