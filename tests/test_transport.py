import pytest

from claude_hop.transport import (
    PROBE_DNS,
    PROBE_NO_AUTH,
    PROBE_TIMEOUT,
    PROBE_UNREACHABLE,
    Transport,
    TransportError,
    classify_ssh_error,
)
from conftest import requires_rsync

# ------------------------------------------- ssh error classification
# stderr strings below are captured from real OpenSSH clients


def test_classify_permission_denied_is_no_auth():
    result = classify_ssh_error(
        "alice@mac.local", "alice@mac.local: Permission denied (publickey,password).\n"
    )
    assert result.verdict == PROBE_NO_AUTH
    assert "ssh-copy-id alice@mac.local" in result.detail
    assert "key-based auth" in result.detail


def test_classify_too_many_auth_failures_is_no_auth():
    result = classify_ssh_error(
        "mac", "Received disconnect from 10.0.0.5 port 22:2: Too many authentication failures\n"
    )
    assert result.verdict == PROBE_NO_AUTH


def test_classify_connect_timeout():
    result = classify_ssh_error(
        "192.0.2.1", "ssh: connect to host 192.0.2.1 port 22: Connection timed out\n"
    )
    assert result.verdict == PROBE_TIMEOUT
    assert "timed out" in result.detail


def test_classify_dns_failure():
    result = classify_ssh_error(
        "nosuch.local",
        "ssh: Could not resolve hostname nosuch.local: Name or service not known\n",
    )
    assert result.verdict == PROBE_DNS
    assert "resolve" in result.detail


def test_classify_connection_refused_is_unreachable():
    result = classify_ssh_error(
        "mac", "ssh: connect to host mac port 22: Connection refused\n"
    )
    assert result.verdict == PROBE_UNREACHABLE
    assert "Connection refused" in result.detail


def test_classify_no_route_is_unreachable():
    result = classify_ssh_error(
        "10.9.9.9", "ssh: connect to host 10.9.9.9 port 22: No route to host\n"
    )
    assert result.verdict == PROBE_UNREACHABLE


def test_classify_empty_stderr_falls_back():
    result = classify_ssh_error("mac", "")
    assert result.verdict == PROBE_UNREACHABLE
    assert "mac" in result.detail


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
