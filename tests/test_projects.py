"""Project inspection: cwd recovery/validation, outside-home detection,
prefix clustering, and git state."""

import json
import shutil
import subprocess

import pytest

from claude_hop import projects
from claude_hop.remap import PathMapper, encode_path

LOCAL = "/home/alice"
MAPPER = PathMapper.for_push(LOCAL, "/Users/alice")


def make_project(root, cwd, *, lines=None, name=None):
    d = root / (name or encode_path(cwd))
    d.mkdir(parents=True, exist_ok=True)
    if lines is None:
        lines = [json.dumps({"type": "user", "cwd": cwd})]
    (d / "s1.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return d


# ------------------------------------------------------------ recover_path


def test_recover_validated_cwd(tmp_path):
    d = make_project(tmp_path, "/mnt/c/Users/tasos/proj")
    assert projects.recover_path(d) == ("/mnt/c/Users/tasos/proj", False)


def test_recover_skips_malformed_lines(tmp_path):
    cwd = "/mnt/c/Users/tasos/proj"
    d = make_project(
        tmp_path, cwd, lines=["not json at all", '{"no":"cwd"}', json.dumps({"cwd": cwd})]
    )
    assert projects.recover_path(d) == (cwd, False)


def test_recover_no_jsonl_is_undetermined(tmp_path):
    d = tmp_path / "-mnt-c-Users-tasos-proj"
    d.mkdir()
    assert projects.recover_path(d) == (None, False)


def test_recover_mismatched_cwd_is_ambiguous(tmp_path):
    d = make_project(tmp_path, "/somewhere/else", name="-mnt-c-Users-tasos-proj")
    assert projects.recover_path(d) == (None, True)


def test_recover_prefers_validating_file_over_mismatch(tmp_path):
    cwd = "/mnt/c/Users/tasos/proj"
    d = make_project(tmp_path, "/somewhere/else", name=encode_path(cwd))
    (d / "s2.jsonl").write_text(json.dumps({"cwd": cwd}) + "\n", encoding="utf-8")
    assert projects.recover_path(d) == (cwd, False)


# --------------------------------------------------------- outside detection


def test_project_under_home_is_not_outside():
    assert not projects.is_outside_home("-home-alice-work-x", LOCAL, MAPPER)


def test_project_outside_home_detected():
    assert projects.is_outside_home("-mnt-c-Users-tasos-proj", LOCAL, MAPPER)


def test_home_prefix_boundary_is_respected():
    # /home/alice2 is NOT under /home/alice
    assert projects.is_outside_home("-home-alice2-proj", LOCAL, MAPPER)


def test_mapped_project_is_not_outside():
    mapper = PathMapper.for_push(LOCAL, "/Users/alice", {"/mnt/c/proj": "/Users/alice/proj"})
    assert not projects.is_outside_home("-mnt-c-proj", LOCAL, mapper)


def test_find_outside_home_classifies_all_three(tmp_path):
    store = tmp_path / "projects"
    make_project(store, f"{LOCAL}/work/inside")
    make_project(store, "/mnt/c/Users/tasos/valid")
    (store / "-opt-nothing-here").mkdir(parents=True)  # undetermined
    make_project(store, "/wrong/place", name="-opt-mismatched")  # ambiguous

    report = projects.find_outside_home(store, LOCAL, MAPPER)
    assert report.validated == {"-mnt-c-Users-tasos-valid": "/mnt/c/Users/tasos/valid"}
    assert report.undetermined == ["-opt-nothing-here"]
    assert report.ambiguous == ["-opt-mismatched"]


# ------------------------------------------------------------- clustering


def test_cluster_single_path_is_itself():
    assert projects.cluster_prefixes(["/mnt/c/Users/tasos/proj"]) == ["/mnt/c/Users/tasos/proj"]


def test_cluster_common_prefix():
    got = projects.cluster_prefixes(
        ["/mnt/c/Users/tasos/proj-a", "/mnt/c/Users/tasos/proj-b"]
    )
    assert got == ["/mnt/c/Users/tasos"]


def test_cluster_disjoint_prefixes_yield_multiple(tmp_path):
    got = projects.cluster_prefixes(
        ["/mnt/c/Users/tasos/a", "/mnt/c/Users/tasos/b", "/opt/work/x", "/opt/work/y"]
    )
    assert got == ["/mnt/c/Users/tasos", "/opt/work"]


def test_snippet_uses_validated_only(tmp_path):
    report = projects.OutsideReport(
        validated={"-mnt-c-Users-tasos-a": "/mnt/c/Users/tasos/a"},
        ambiguous=["-opt-mismatched"],
    )
    snippet = projects.mapping_snippet(report, "mac", "/Users/demo")
    assert '"/mnt/c/Users/tasos/a" = "/Users/demo/a"' in snippet
    assert "mismatched" not in snippet


# ------------------------------------------------------------------- git

requires_git = pytest.mark.skipif(shutil.which("git") is None, reason="git not installed")


def git(path, *args):
    subprocess.run(
        ["git", "-C", str(path), *args],
        check=True,
        capture_output=True,
        env={
            "GIT_AUTHOR_NAME": "t",
            "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t",
            "GIT_COMMITTER_EMAIL": "t@t",
            "HOME": str(path),
            "PATH": "/usr/bin:/bin:/usr/local/bin",
        },
    )


def make_repo(tmp_path, name):
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-q", "-b", "main")
    (repo / "f.txt").write_text("v1\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "init")
    return repo


@requires_git
def test_git_state_clean_with_upstream(tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo = make_repo(tmp_path, "clean")
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-qu", "origin", "main")
    state = projects.git_state(str(repo))
    assert state == projects.GitState(path=str(repo), dirty=False, ahead=0)
    assert not state.noteworthy


@requires_git
def test_git_state_dirty(tmp_path):
    repo = make_repo(tmp_path, "dirty")
    (repo / "f.txt").write_text("changed\n", encoding="utf-8")
    state = projects.git_state(str(repo))
    assert state.dirty
    assert state.noteworthy


@requires_git
def test_git_state_ahead_of_upstream(tmp_path):
    bare = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", str(bare)], check=True)
    repo = make_repo(tmp_path, "ahead")
    git(repo, "remote", "add", "origin", str(bare))
    git(repo, "push", "-qu", "origin", "main")
    (repo / "f.txt").write_text("v2\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "second")
    state = projects.git_state(str(repo))
    assert not state.dirty
    assert state.ahead == 1
    assert state.noteworthy


@requires_git
def test_git_state_no_upstream(tmp_path):
    repo = make_repo(tmp_path, "loner")
    state = projects.git_state(str(repo))
    assert state.ahead is None
    assert state.noteworthy  # nothing has traveled via git


@requires_git
def test_git_state_not_a_repo(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    assert projects.git_state(str(plain)) is None


def test_git_state_missing_binary(tmp_path, monkeypatch):
    monkeypatch.setattr(shutil, "which", lambda name: None)
    assert projects.git_state(str(tmp_path)) is None


@requires_git
def test_git_warnings_only_where_noteworthy(tmp_path):
    store = tmp_path / "projects"
    dirty_repo = make_repo(tmp_path, "dirtyrepo")
    (dirty_repo / "f.txt").write_text("x\n", encoding="utf-8")
    make_project(store, str(dirty_repo))
    make_project(store, str(tmp_path / "no-such-dir"))  # path gone: silent skip
    (store / "-opt-empty").mkdir()  # undetermined: silent skip

    warnings = projects.git_warnings(store)
    assert [name for name, _ in warnings] == [encode_path(str(dirty_repo))]
    assert warnings[0][1].dirty