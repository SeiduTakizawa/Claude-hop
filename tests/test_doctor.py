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


class FakeSshTransport:
    """Duck-typed stand-in for Transport in clock-skew tests."""

    host = "mac.local"

    def __init__(self, stdout, returncode=0):
        self._stdout = stdout
        self._returncode = returncode

    def run_ssh(self, command, timeout=10):
        import subprocess

        return subprocess.CompletedProcess([], self._returncode, stdout=self._stdout, stderr="")


def test_clock_skew_ok_when_in_sync():
    import time

    checks = []
    doctor._check_clock_skew(checks, FakeSshTransport(str(int(time.time()))))
    assert checks[0].name == "clock skew"
    assert checks[0].status == doctor.OK


def test_clock_skew_warns_beyond_threshold():
    import time

    checks = []
    doctor._check_clock_skew(checks, FakeSshTransport(str(int(time.time()) - 120)))
    assert checks[0].status == doctor.WARN
    assert "newer-wins merging may skip updates" in checks[0].detail
    assert "clock skew of 1" in checks[0].detail  # ~120s, allow 119-121


def test_clock_skew_silent_on_ssh_failure():
    checks = []
    doctor._check_clock_skew(checks, FakeSshTransport("", returncode=255))
    assert checks == []


def test_clock_skew_silent_on_garbage_output():
    checks = []
    doctor._check_clock_skew(checks, FakeSshTransport("not-a-number\n"))
    assert checks == []


def test_local_remote_has_no_clock_skew_check(homes, tmp_path, quiet_env):
    local, remote = homes
    checks = by_name(doctor.run_checks(write_cfg(tmp_path, remote), local))
    assert "clock skew" not in checks


def test_project_coverage_warns_on_outside_home(homes, tmp_path, quiet_env):
    local, remote = homes
    d = local / ".claude" / "projects" / "-mnt-c-Users-tasos-proj"
    d.mkdir(parents=True)
    (d / "s1.jsonl").write_text('{"cwd":"/mnt/c/Users/tasos/proj"}\n', encoding="utf-8")
    checks = by_name(doctor.run_checks(write_cfg(tmp_path, remote), local))
    assert checks["project coverage"].status == doctor.WARN
    assert "-mnt-c-Users-tasos-proj" in checks["project coverage"].detail


def test_project_coverage_ok_when_mapped(homes, tmp_path, quiet_env):
    local, remote = homes
    d = local / ".claude" / "projects" / "-mnt-c-Users-tasos-proj"
    d.mkdir(parents=True)
    (d / "s1.jsonl").write_text('{"cwd":"/mnt/c/Users/tasos/proj"}\n', encoding="utf-8")
    path = write_cfg(tmp_path, remote, {"/mnt/c/Users/tasos": f"{remote}/tasos"})
    checks = by_name(doctor.run_checks(path, local))
    assert checks["project coverage"].status == doctor.OK


def test_other_stores_note(tmp_path):
    fake_home_root = tmp_path / "home"
    other = fake_home_root / "tasos" / ".claude" / "projects" / "-x"
    other.mkdir(parents=True)
    mine = fake_home_root / "me"
    (mine / ".claude" / "projects").mkdir(parents=True)
    checks = []
    doctor._check_other_stores(checks, mine, roots=(str(fake_home_root),), euid=1000)
    assert len(checks) == 1
    assert checks[0].status == doctor.OK
    assert "claude-hop only syncs the store of the current user" in checks[0].detail
    assert str(fake_home_root / "tasos" / ".claude") in checks[0].detail


def test_other_stores_warns_as_root(tmp_path):
    fake_home_root = tmp_path / "home"
    other = fake_home_root / "tasos" / ".claude" / "projects" / "-x"
    other.mkdir(parents=True)
    root_home = tmp_path / "root"
    (root_home / ".claude" / "projects").mkdir(parents=True)
    checks = []
    doctor._check_other_stores(checks, root_home, roots=(str(fake_home_root),), euid=0)
    assert checks[0].status == doctor.WARN
    assert "running as root" in checks[0].detail


def test_other_stores_silent_when_none(tmp_path):
    fake_home_root = tmp_path / "home"
    mine = fake_home_root / "me"
    (mine / ".claude" / "projects" / "-p").mkdir(parents=True)
    checks = []
    doctor._check_other_stores(checks, mine, roots=(str(fake_home_root),), euid=1000)
    assert checks == []


def test_other_stores_skips_empty_and_missing(tmp_path):
    fake_home_root = tmp_path / "home"
    (fake_home_root / "empty" / ".claude" / "projects").mkdir(parents=True)  # no sessions
    (fake_home_root / "noclaude").mkdir(parents=True)
    mine = fake_home_root / "me"
    (mine / ".claude" / "projects").mkdir(parents=True)
    checks = []
    doctor._check_other_stores(checks, mine, roots=(str(fake_home_root),), euid=1000)
    assert checks == []


def test_stale_temp_dirs_warn(homes, tmp_path, quiet_env):
    local, remote = homes
    (quiet_env / "claude-hop-abc123").mkdir()
    checks = by_name(doctor.run_checks(write_cfg(tmp_path, remote), local))
    assert checks["temp dirs"].status == doctor.WARN
    assert "claude-hop-abc123" in checks["temp dirs"].detail
