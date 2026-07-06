"""Integration tests: real push/pull against a 'remote' that is a local path
(host = ""), exercising staging, remapping, and rsync merge semantics."""

import os
import tarfile

import pytest

from claude_hop import sync
from claude_hop.remap import encode_path
from conftest import local_cfg, make_session, requires_rsync, session_cwd

pytestmark = requires_rsync

OLD = 1_600_000_000
NEW = 1_700_000_000


def remote_project(remote_home, rel):
    return remote_home / ".claude" / "projects" / encode_path(f"{remote_home}/{rel}")


def test_push_creates_remapped_remote(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    report = sync.push(local_cfg(remote), local_home=local)
    out = remote_project(remote, "work/webshop") / "s1.jsonl"
    assert out.exists()
    assert session_cwd(out) == f"{remote}/work/webshop"
    assert report.total_files == 1
    assert not report.dry_run


def test_push_dry_run_writes_nothing(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    report = sync.push(local_cfg(remote), local_home=local, dry_run=True)
    assert report.dry_run
    assert report.total_files == 1
    assert not (remote / ".claude").exists()


def test_push_merge_newer_remote_file_wins(homes, not_running):
    local, remote = homes
    f = make_session(local, "work/webshop")
    os.utime(f, (OLD, OLD))
    target = remote_project(remote, "work/webshop")
    target.mkdir(parents=True)
    remote_file = target / "s1.jsonl"
    remote_file.write_text("remote version — newer\n", encoding="utf-8")
    os.utime(remote_file, (NEW, NEW))

    report = sync.push(local_cfg(remote), local_home=local)
    assert remote_file.read_text(encoding="utf-8") == "remote version — newer\n"
    assert report.total_files == 0


def test_push_merge_older_remote_file_updated(homes, not_running):
    local, remote = homes
    f = make_session(local, "work/webshop")
    os.utime(f, (NEW, NEW))
    target = remote_project(remote, "work/webshop")
    target.mkdir(parents=True)
    remote_file = target / "s1.jsonl"
    remote_file.write_text("remote version — older\n", encoding="utf-8")
    os.utime(remote_file, (OLD, OLD))

    report = sync.push(local_cfg(remote), local_home=local)
    assert session_cwd(remote_file) == f"{remote}/work/webshop"
    assert report.summary[encode_path(f"{remote}/work/webshop")].updated == 1


def test_push_never_deletes_on_target(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    target = remote_project(remote, "work/webshop")
    target.mkdir(parents=True)
    remote_only = target / "remote-only.jsonl"
    remote_only.write_text("exists only on remote\n", encoding="utf-8")

    sync.push(local_cfg(remote), local_home=local)
    assert remote_only.exists()


def test_push_applies_specific_mapping(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    mappings = {f"{local}/work/webshop": f"{remote}/projects/webshop"}
    sync.push(local_cfg(remote, mappings), local_home=local)
    out = remote / ".claude" / "projects" / encode_path(f"{remote}/projects/webshop") / "s1.jsonl"
    assert out.exists()
    assert session_cwd(out) == f"{remote}/projects/webshop"


def test_push_refuses_while_claude_running(homes, monkeypatch):
    local, remote = homes
    make_session(local, "work/webshop")
    monkeypatch.setattr(sync, "claude_running", lambda: True)
    with pytest.raises(sync.SyncError, match="running"):
        sync.push(local_cfg(remote), local_home=local)
    # override flag works
    sync.push(local_cfg(remote), local_home=local, force=True)
    assert (remote_project(remote, "work/webshop") / "s1.jsonl").exists()


def test_push_without_local_sessions(homes, not_running):
    local, remote = homes
    with pytest.raises(sync.SyncError, match="no local sessions"):
        sync.push(local_cfg(remote), local_home=local)


def test_pull_remaps_remote_sessions(homes, not_running):
    local, remote = homes
    make_session(remote, "work/webshop")  # remote-style session, paths under remote home
    report = sync.pull(local_cfg(remote), local_home=local)
    out = local / ".claude" / "projects" / encode_path(f"{local}/work/webshop") / "s1.jsonl"
    assert out.exists()
    assert session_cwd(out) == f"{local}/work/webshop"
    assert report.total_files == 1


def test_pull_without_remote_sessions(homes, not_running):
    local, remote = homes
    with pytest.raises(sync.SyncError, match="no sessions on the remote"):
        sync.pull(local_cfg(remote), local_home=local)


def test_pull_first_time_offers_backup(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    make_session(remote, "work/beta")
    asked = []

    def confirm(msg):
        asked.append(msg)
        return True

    report = sync.pull(local_cfg(remote), local_home=local, confirm=confirm)
    assert len(asked) == 1
    assert report.backup is not None and report.backup.exists()
    with tarfile.open(report.backup) as tar:
        names = tar.getnames()
    assert any("s1.jsonl" in n for n in names)

    # second pull: marker set, no new offer
    make_session(remote, "work/gamma")
    report2 = sync.pull(local_cfg(remote), local_home=local, confirm=confirm)
    assert len(asked) == 1
    assert report2.backup is None


def test_pull_backup_declined(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    make_session(remote, "work/beta")
    report = sync.pull(local_cfg(remote), local_home=local, confirm=lambda m: False)
    assert report.backup is None
    assert not (local / ".claude" / "backups").exists()


def test_pull_into_empty_local_no_backup_offer(homes, not_running):
    local, remote = homes
    make_session(remote, "work/beta")
    asked = []
    sync.pull(local_cfg(remote), local_home=local, confirm=lambda m: asked.append(m) or True)
    assert asked == []


def test_pull_dry_run_writes_nothing(homes, not_running):
    local, remote = homes
    make_session(remote, "work/beta")
    report = sync.pull(local_cfg(remote), local_home=local, dry_run=True)
    assert report.dry_run
    assert report.total_files == 1
    assert list((local / ".claude" / "projects").iterdir()) == []
    assert not (local / ".claude" / sync._PULL_MARKER).exists()


def test_full_round_trip_merge(homes, not_running):
    """Push from A, add a session on B, pull on A: both sides converge."""
    local, remote = homes
    make_session(local, "work/webshop")
    sync.push(local_cfg(remote), local_home=local)

    # a new session appears on the remote machine, in its own path scheme
    make_session(remote, "work/webshop", name="s2.jsonl")
    make_session(remote, "other/beta")

    sync.pull(local_cfg(remote), local_home=local, confirm=lambda m: True)
    webshop_local = local / ".claude" / "projects" / encode_path(f"{local}/work/webshop")
    assert (webshop_local / "s1.jsonl").exists()
    assert (webshop_local / "s2.jsonl").exists()
    assert session_cwd(webshop_local / "s2.jsonl") == f"{local}/work/webshop"
    beta = local / ".claude" / "projects" / encode_path(f"{local}/other/beta") / "s1.jsonl"
    assert session_cwd(beta) == f"{local}/other/beta"
