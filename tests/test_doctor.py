import tempfile

import pytest

from claude_hop import config as config_mod
from claude_hop import doctor, sync
from claude_hop.config import Config, Remote
from conftest import local_cfg, make_session


@pytest.fixture
def quiet_env(monkeypatch, tmp_path):
    """No stale temp dirs, Claude not running."""
    fake_tmp = tmp_path / "faketmp"
    fake_tmp.mkdir()
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(fake_tmp))
    monkeypatch.setattr(sync, "claude_running", lambda: False)
    return fake_tmp


def by_name(checks):
    return {c.name: c for c in checks}


def write_cfg(tmp_path, remote_home, mappings=None):
    path = tmp_path / "config.toml"
    config_mod.save(local_cfg(remote_home, mappings), path)
    return path


def test_healthy_local_mode(homes, tmp_path, quiet_env):
    local, remote = homes
    make_session(local, "work/webshop")
    (remote / ".claude" / "projects").mkdir(parents=True)
    checks = by_name(doctor.run_checks(write_cfg(tmp_path, remote), local))

    assert checks["config"].status == doctor.OK
    assert checks["mappings"].status == doctor.OK
    assert checks["local sessions"].status == doctor.OK
    assert "1 project(s)" in checks["local sessions"].detail
    assert checks["ssh"].status == doctor.OK
    assert "local-path" in checks["ssh"].detail
    assert checks["remote sessions"].status == doctor.OK
    assert checks["claude code"].status == doctor.OK
    assert checks["temp dirs"].status == doctor.OK
    assert not any(c.status == doctor.FAIL for c in checks.values())


def test_missing_config_fails_and_skips_remote(homes, tmp_path, quiet_env):
    local, _ = homes
    checks = by_name(doctor.run_checks(tmp_path / "missing.toml", local))
    assert checks["config"].status == doctor.FAIL
    assert "ssh" not in checks
    assert "claude code" in checks  # local checks still run


def test_ambiguous_mappings_fail(homes, tmp_path, quiet_env):
    local, remote = homes
    # distinct paths that encode to the same dirname with different targets
    mappings = {
        f"{local}/a.b": f"{remote}/x",
        f"{local}/a-b": f"{remote}/y",
    }
    checks = by_name(doctor.run_checks(write_cfg(tmp_path, remote, mappings), local))
    assert checks["mappings"].status == doctor.FAIL
    assert "ambiguous" in checks["mappings"].detail


def test_no_local_sessions_warns(homes, tmp_path, quiet_env):
    local, remote = homes
    (local / ".claude" / "projects").rmdir()
    checks = by_name(doctor.run_checks(write_cfg(tmp_path, remote), local))
    assert checks["local sessions"].status == doctor.WARN


def test_missing_remote_projects_warns(homes, tmp_path, quiet_env):
    local, remote = homes
    checks = by_name(doctor.run_checks(write_cfg(tmp_path, remote), local))
    assert checks["remote sessions"].status == doctor.WARN
    assert "first push" in checks["remote sessions"].detail


def test_claude_running_warns(homes, tmp_path, quiet_env, monkeypatch):
    local, remote = homes
    monkeypatch.setattr(sync, "claude_running", lambda: True)
    checks = by_name(doctor.run_checks(write_cfg(tmp_path, remote), local))
    assert checks["claude code"].status == doctor.WARN
    assert "exit" in checks["claude code"].detail


def write_cfg2(tmp_path, home_a, home_b):
    cfg = Config(
        remotes={
            "a": Remote(name="a", host="", home=str(home_a)),
            "b": Remote(name="b", host="", home=str(home_b)),
        }
    )
    path = tmp_path / "config.toml"
    config_mod.save(cfg, path)
    return path


def test_doctor_checks_every_remote_by_default(homes, tmp_path, quiet_env):
    local, remote = homes
    second = tmp_path / "second-home"
    second.mkdir()
    checks = doctor.run_checks(write_cfg2(tmp_path, remote, second), local)
    names = [c.name for c in checks]
    assert "ssh (a)" in names and "ssh (b)" in names
    assert "mappings (a)" in names and "mappings (b)" in names


def test_doctor_single_remote_by_name(homes, tmp_path, quiet_env):
    local, remote = homes
    second = tmp_path / "second-home"
    second.mkdir()
    checks = doctor.run_checks(write_cfg2(tmp_path, remote, second), local, "b")
    names = [c.name for c in checks]
    assert "ssh" in names  # no suffix for a single target
    assert "ssh (a)" not in names and "ssh (b)" not in names


def test_doctor_unknown_remote_raises_like_other_commands(homes, tmp_path, quiet_env):
    local, remote = homes
    with pytest.raises(config_mod.ConfigError, match=r"unknown remote 'nope'.*available"):
        doctor.run_checks(write_cfg(tmp_path, remote), local, "nope")


def test_stale_temp_dirs_warn(homes, tmp_path, quiet_env):
    local, remote = homes
    (quiet_env / "claude-hop-abc123").mkdir()
    checks = by_name(doctor.run_checks(write_cfg(tmp_path, remote), local))
    assert checks["temp dirs"].status == doctor.WARN
    assert "claude-hop-abc123" in checks["temp dirs"].detail
