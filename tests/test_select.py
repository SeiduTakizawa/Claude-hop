"""Single-project selection: selector resolution and selective push/pull."""

import os

import pytest

from claude_hop import sync
from claude_hop.remap import encode_path
from conftest import local_cfg, make_session, requires_rsync, session_cwd

# ------------------------------------------------------ selector resolution


def test_resolve_encoded_name_passes_through():
    assert sync.resolve_projects(["-home-alice-work-webshop"]) == ["-home-alice-work-webshop"]


def test_resolve_absolute_path():
    assert sync.resolve_projects(["/home/alice/work/webshop"]) == [
        encode_path("/home/alice/work/webshop")
    ]


def test_resolve_trailing_slash_normalized():
    assert sync.resolve_projects(["/home/alice/work/webshop/"]) == [
        encode_path("/home/alice/work/webshop")
    ]


def test_resolve_dot_is_cwd(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    assert sync.resolve_projects(["."]) == [encode_path(os.getcwd())]


def test_resolve_relative_path(monkeypatch, tmp_path):
    (tmp_path / "work").mkdir()
    monkeypatch.chdir(tmp_path)
    assert sync.resolve_projects(["work"]) == [encode_path(os.path.join(os.getcwd(), "work"))]


def test_resolve_dedupes_preserving_order():
    sels = ["/home/a/x", "-home-a-y", "/home/a/x/"]
    assert sync.resolve_projects(sels) == ["-home-a-x", "-home-a-y"]


def test_resolve_skips_empty():
    assert sync.resolve_projects(["", "  "]) == []


# ------------------------------------------------------------ selective push


@requires_rsync
def test_push_single_project(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    make_session(local, "work/blog")
    report = sync.push(local_cfg(remote), local_home=local, projects=[f"{local}/work/webshop"])
    remote_projects = remote / ".claude" / "projects"
    assert (remote_projects / encode_path(f"{remote}/work/webshop")).is_dir()
    assert not (remote_projects / encode_path(f"{remote}/work/blog")).exists()
    assert report.total_files == 1


@requires_rsync
def test_push_multiple_projects_by_encoded_name(homes, not_running):
    local, remote = homes
    for rel in ("work/webshop", "work/blog", "work/docs"):
        make_session(local, rel)
    names = [encode_path(f"{local}/work/webshop"), encode_path(f"{local}/work/docs")]
    sync.push(local_cfg(remote), local_home=local, projects=names)
    remote_projects = remote / ".claude" / "projects"
    assert (remote_projects / encode_path(f"{remote}/work/webshop")).is_dir()
    assert (remote_projects / encode_path(f"{remote}/work/docs")).is_dir()
    assert not (remote_projects / encode_path(f"{remote}/work/blog")).exists()


@requires_rsync
def test_push_unknown_project_suggests_closest(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    with pytest.raises(sync.SyncError, match="did you mean"):
        sync.push(local_cfg(remote), local_home=local, projects=[f"{local}/work/webshopp"])


@requires_rsync
def test_push_selection_skips_extras(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    (local / ".claude" / "history.jsonl").write_text("{}\n", encoding="utf-8")
    cfg = local_cfg(remote)
    cfg.include_history = True
    sync.push(cfg, local_home=local, projects=[f"{local}/work/webshop"])
    assert not (remote / ".claude" / "history.jsonl").exists()
    # and a full push still includes it
    sync.push(cfg, local_home=local)
    assert (remote / ".claude" / "history.jsonl").exists()


@requires_rsync
def test_push_selection_with_mapping(homes, not_running):
    local, remote = homes
    make_session(local, "work/webshop")
    mappings = {f"{local}/work/webshop": f"{remote}/projects/webshop"}
    sync.push(local_cfg(remote, mappings), local_home=local, projects=[f"{local}/work/webshop"])
    out = remote / ".claude" / "projects" / encode_path(f"{remote}/projects/webshop") / "s1.jsonl"
    assert session_cwd(out) == f"{remote}/projects/webshop"


# ------------------------------------------------------------ selective pull


@requires_rsync
def test_pull_single_project(homes, not_running):
    local, remote = homes
    make_session(remote, "work/webshop")
    make_session(remote, "work/blog")
    report = sync.pull(local_cfg(remote), local_home=local, projects=[f"{local}/work/webshop"])
    local_projects = local / ".claude" / "projects"
    out = local_projects / encode_path(f"{local}/work/webshop") / "s1.jsonl"
    assert session_cwd(out) == f"{local}/work/webshop"
    assert not (local_projects / encode_path(f"{local}/work/blog")).exists()
    assert report.total_files == 1


@requires_rsync
def test_pull_project_that_only_exists_remotely(homes, not_running):
    # selecting by local path works even before the project exists locally
    local, remote = homes
    make_session(remote, "work/newthing")
    sync.pull(local_cfg(remote), local_home=local, projects=[f"{local}/work/newthing"])
    out = local / ".claude" / "projects" / encode_path(f"{local}/work/newthing") / "s1.jsonl"
    assert out.exists()


@requires_rsync
def test_pull_missing_remote_project_errors(homes, not_running):
    local, remote = homes
    make_session(remote, "work/webshop")
    with pytest.raises(sync.SyncError, match="not found on the remote"):
        sync.pull(local_cfg(remote), local_home=local, projects=[f"{local}/work/nope"])


@requires_rsync
def test_pull_selection_skips_extras(homes, not_running):
    local, remote = homes
    make_session(remote, "work/webshop")
    (remote / ".claude" / "history.jsonl").write_text("{}\n", encoding="utf-8")
    cfg = local_cfg(remote)
    cfg.include_history = True
    sync.pull(cfg, local_home=local, projects=[f"{local}/work/webshop"])
    assert not (local / ".claude" / "history.jsonl").exists()


@requires_rsync
def test_pull_selection_with_mapping(homes, not_running):
    local, remote = homes
    make_session(remote, "projects/webshop")
    mappings = {f"{local}/work/webshop": f"{remote}/projects/webshop"}
    sync.pull(
        local_cfg(remote, mappings), local_home=local, projects=[f"{local}/work/webshop"]
    )
    out = local / ".claude" / "projects" / encode_path(f"{local}/work/webshop") / "s1.jsonl"
    assert session_cwd(out) == f"{local}/work/webshop"