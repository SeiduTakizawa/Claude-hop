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


def test_push_includes_history_when_enabled(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    history = local / ".claude" / "history.jsonl"
    history.write_text(
        f'{{"display":"do it","project":"{local}/work/webshop"}}\n', encoding="utf-8"
    )

    cfg = local_cfg(remote)
    cfg.include_history = True
    report = sync.push(cfg, local_home=local)
    remote_history = remote / ".claude" / "history.jsonl"
    assert f"{remote}/work/webshop" in remote_history.read_text(encoding="utf-8")
    assert "history.jsonl" in report.summary


def test_push_skips_history_by_default(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    (local / ".claude" / "history.jsonl").write_text("{}\n", encoding="utf-8")
    sync.push(local_cfg(remote), local_home=local)
    assert not (remote / ".claude" / "history.jsonl").exists()


def test_push_and_pull_agents_verbatim(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    agents = local / ".claude" / "agents"
    agents.mkdir(parents=True)
    (agents / "helper.md").write_text(f"works on {local}/work/webshop\n", encoding="utf-8")

    cfg = local_cfg(remote)
    cfg.include_agents = True
    report = sync.push(cfg, local_home=local)
    out = remote / ".claude" / "agents" / "helper.md"
    # verbatim: agents are not remapped
    assert out.read_text(encoding="utf-8") == f"works on {local}/work/webshop\n"
    assert "agents" in report.summary

    # and back: a new agent on the remote comes home on pull
    (remote / ".claude" / "agents" / "reviewer.md").write_text("review\n", encoding="utf-8")
    sync.pull(cfg, local_home=local, confirm=lambda m: True)
    assert (agents / "reviewer.md").read_text(encoding="utf-8") == "review\n"


def test_pull_history_remapped(homes, not_running):
    local, remote = homes
    make_session(remote, "work/webshop")
    (remote / ".claude" / "history.jsonl").write_text(
        f'{{"project":"{remote}/work/webshop"}}\n', encoding="utf-8"
    )
    cfg = local_cfg(remote)
    cfg.include_history = True
    sync.pull(cfg, local_home=local)
    local_history = local / ".claude" / "history.jsonl"
    assert f"{local}/work/webshop" in local_history.read_text(encoding="utf-8")
    assert str(remote) not in local_history.read_text(encoding="utf-8")


def test_resumed_session_update_lands_on_second_pull(homes, not_running):
    """The field scenario: pull, resume on the remote (newer mtime + new
    content), pull again — the update must land, with its mtime intact."""
    local, remote = homes
    remote_file = make_session(remote, "work/webshop")
    os.utime(remote_file, (OLD, OLD))
    sync.pull(local_cfg(remote), local_home=local, confirm=lambda m: True)

    local_file = (
        local / ".claude" / "projects" / encode_path(f"{local}/work/webshop") / "s1.jsonl"
    )
    assert abs(local_file.stat().st_mtime - OLD) < 1  # 1s window: fs granularity varies

    # "resume" on the remote: new content, newer mtime
    remote_file.write_text(
        f'{{"cwd":"{remote}/work/webshop","msg":"resumed over there"}}\n', encoding="utf-8"
    )
    os.utime(remote_file, (NEW, NEW))

    sync.pull(local_cfg(remote), local_home=local, confirm=lambda m: True)
    text = local_file.read_text(encoding="utf-8")
    assert "resumed over there" in text
    assert f"{local}/work/webshop" in text  # remapped on the way in
    assert abs(local_file.stat().st_mtime - NEW) < 1


def test_verbose_reports_exact_skipped_newer_set(homes, not_running):
    """--verbose must list exactly the files -u skipped, no more, no less."""
    local, remote = homes
    for name in ("kept.jsonl", "updated.jsonl", "created.jsonl"):
        f = make_session(local, "work/webshop", name=name)
        os.utime(f, (OLD, OLD))
    target = remote_project(remote, "work/webshop")
    target.mkdir(parents=True)
    # remote already has a NEWER kept.jsonl (must be skipped and reported)
    # and an OLDER updated.jsonl (must be overwritten, not reported)
    newer = target / "kept.jsonl"
    newer.write_text("remote is newer\n", encoding="utf-8")
    os.utime(newer, (NEW, NEW))
    older = target / "updated.jsonl"
    older.write_text("remote is older\n", encoding="utf-8")
    os.utime(older, (OLD - 1000, OLD - 1000))

    report = sync.push(local_cfg(remote), local_home=local, verbose=True)
    project = encode_path(f"{remote}/work/webshop")
    assert report.skipped == [f"{project}/kept.jsonl"]  # exact
    assert newer.read_text(encoding="utf-8") == "remote is newer\n"  # untouched
    assert "remote is older" not in older.read_text(encoding="utf-8")  # replaced


def test_verbose_off_reports_nothing_skipped(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    report = sync.push(local_cfg(remote), local_home=local)
    assert report.skipped == []


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
