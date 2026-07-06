import pytest

from claude_hop.transport import Transport, TransportError
from conftest import requires_rsync


def test_remote_projects_path():
    t = Transport(host="mac.local", remote_home="/Users/alice")
    assert t.remote_projects == "/Users/alice/.claude/projects"


def test_remote_arg_quotes_for_remote_shell():
    t = Transport(host="mac.local", remote_home="/Users/al ice")
    assert t.remote_projects_arg == "mac.local:'/Users/al ice/.claude/projects'"


def test_local_mode_arg_is_plain_path():
    t = Transport(host="", remote_home="/mnt/fake")
    assert t.remote_projects_arg == "/mnt/fake/.claude/projects"


def test_run_ssh_rejected_in_local_mode():
    t = Transport(host="", remote_home="/mnt/fake")
    with pytest.raises(TransportError, match="local-path mode"):
        t.run_ssh("true")


def test_local_ensure_and_exists(tmp_path):
    t = Transport(host="", remote_home=str(tmp_path / "fake-home"))
    assert not t.remote_projects_exists()
    t.ensure_remote_projects()
    assert t.remote_projects_exists()


@requires_rsync
def test_rsync_merge_and_itemize(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "a.jsonl").write_text("a", encoding="utf-8")
    (src / "skip.tmp").write_text("tmp", encoding="utf-8")
    t = Transport(host="", remote_home=str(tmp_path))

    changes = t.rsync(f"{src}/", f"{dst}/")
    assert (dst / "a.jsonl").read_text(encoding="utf-8") == "a"
    assert not (dst / "skip.tmp").exists()  # *.tmp excluded
    assert any("a.jsonl" in line for line in changes)


@requires_rsync
def test_rsync_dry_run_writes_nothing(tmp_path):
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.mkdir()
    (src / "a.jsonl").write_text("a", encoding="utf-8")
    t = Transport(host="", remote_home=str(tmp_path))
    changes = t.rsync(f"{src}/", f"{dst}/", dry_run=True)
    assert not dst.exists()
    assert any("a.jsonl" in line for line in changes)


@requires_rsync
def test_rsync_failure_raises(tmp_path):
    t = Transport(host="", remote_home=str(tmp_path))
    with pytest.raises(TransportError, match="rsync failed"):
        t.rsync(f"{tmp_path}/does-not-exist/", f"{tmp_path}/dst/")
