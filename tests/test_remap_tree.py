import os
from pathlib import Path

from claude_hop.remap import PathMapper, remap_tree

LOCAL = "/home/alice"
REMOTE = "/Users/alice"

SESSION = (
    '{"type":"user","cwd":"/home/alice/work/webshop"}\n'
    '{"type":"assistant","file":"/home/alice/work/webshop/src/main.py"}\n'
    "malformed line mentioning /home/alice/work/webshop stays a line\n"
    '{"unicode":"héllo /home/alice/δoc"}\n'
)
SESSION_REMAPPED = SESSION.replace("/home/alice", "/Users/alice")

INVALID_UTF8 = b'{"cwd":"/home/alice/work/webshop"}\n\xff\xfe broken\n'


def make_projects(root: Path) -> Path:
    src = root / "projects"
    webshop = src / "-home-alice-work-webshop"
    webshop.mkdir(parents=True)
    (webshop / "session-1.jsonl").write_text(SESSION, encoding="utf-8")
    (webshop / "notes.txt").write_text("verbatim /home/alice/work/webshop", encoding="utf-8")
    sub = webshop / "subagents"
    sub.mkdir()
    (sub / "agent.jsonl").write_text(SESSION, encoding="utf-8")
    other = src / "-tmp-scratch"
    other.mkdir()
    (other / "s.jsonl").write_text('{"cwd":"/tmp/scratch"}\n', encoding="utf-8")
    (src / "stray-root-file").write_text("not a project dir", encoding="utf-8")
    return src


def test_push_remaps_dirs_and_jsonl(tmp_path):
    src = make_projects(tmp_path)
    dest = tmp_path / "staging"
    remap_tree(src, dest, PathMapper.for_push(LOCAL, REMOTE))

    assert sorted(p.name for p in dest.iterdir()) == [
        "-Users-alice-work-webshop",
        "-tmp-scratch",
    ]
    webshop = dest / "-Users-alice-work-webshop"
    assert (webshop / "session-1.jsonl").read_text(encoding="utf-8") == SESSION_REMAPPED
    assert (webshop / "subagents" / "agent.jsonl").read_text(encoding="utf-8") == SESSION_REMAPPED
    # non-jsonl files are copied verbatim
    notes = (webshop / "notes.txt").read_text(encoding="utf-8")
    assert notes == "verbatim /home/alice/work/webshop"
    # projects outside every mapping keep their name and contents
    scratch = (dest / "-tmp-scratch" / "s.jsonl").read_text(encoding="utf-8")
    assert scratch == '{"cwd":"/tmp/scratch"}\n'


def test_source_tree_untouched(tmp_path):
    src = make_projects(tmp_path)
    before = {str(p.relative_to(src)): p.read_bytes() for p in src.rglob("*") if p.is_file()}
    remap_tree(src, tmp_path / "staging", PathMapper.for_push(LOCAL, REMOTE))
    after = {str(p.relative_to(src)): p.read_bytes() for p in src.rglob("*") if p.is_file()}
    assert before == after


def test_stray_root_files_skipped(tmp_path):
    src = make_projects(tmp_path)
    dest = tmp_path / "staging"
    remap_tree(src, dest, PathMapper.for_push(LOCAL, REMOTE))
    assert not (dest / "stray-root-file").exists()


def test_mtime_preserved_on_rewritten_jsonl(tmp_path):
    src = make_projects(tmp_path)
    f = src / "-home-alice-work-webshop" / "session-1.jsonl"
    stamp = 1_600_000_000
    os.utime(f, (stamp, stamp))
    dest = tmp_path / "staging"
    remap_tree(src, dest, PathMapper.for_push(LOCAL, REMOTE))
    out = dest / "-Users-alice-work-webshop" / "session-1.jsonl"
    assert int(out.stat().st_mtime) == stamp


def test_mtime_preserved_full_precision_through_remap(tmp_path):
    """The local remap step must not lose a single nanosecond — rsync -u
    merges depend on rewritten files carrying the ORIGINAL mtime."""
    src = make_projects(tmp_path)
    stamp_ns = 1_600_000_000_123_456_789
    rewritten = src / "-home-alice-work-webshop" / "session-1.jsonl"
    verbatim = src / "-home-alice-work-webshop" / "notes.txt"
    os.utime(rewritten, ns=(stamp_ns, stamp_ns))
    os.utime(verbatim, ns=(stamp_ns, stamp_ns))
    dest = tmp_path / "staging"
    remap_tree(src, dest, PathMapper.for_push(LOCAL, REMOTE))
    out = dest / "-Users-alice-work-webshop"
    assert (out / "session-1.jsonl").stat().st_mtime_ns == stamp_ns
    assert (out / "notes.txt").stat().st_mtime_ns == stamp_ns


def test_invalid_utf8_jsonl_copied_byte_identical(tmp_path):
    src = tmp_path / "projects"
    proj = src / "-home-alice-work-webshop"
    proj.mkdir(parents=True)
    (proj / "broken.jsonl").write_bytes(INVALID_UTF8)
    dest = tmp_path / "staging"
    remap_tree(src, dest, PathMapper.for_push(LOCAL, REMOTE))
    assert (dest / "-Users-alice-work-webshop" / "broken.jsonl").read_bytes() == INVALID_UTF8


def test_tree_round_trip_is_identity(tmp_path):
    mappings = {"/home/alice/work/webshop": "/Users/alice/projects/webshop"}
    src = make_projects(tmp_path)
    pushed = tmp_path / "pushed"
    pulled = tmp_path / "pulled"
    remap_tree(src, pushed, PathMapper.for_push(LOCAL, REMOTE, mappings))
    remap_tree(pushed, pulled, PathMapper.for_pull(LOCAL, REMOTE, mappings))

    original = {
        str(p.relative_to(src)): p.read_bytes() for p in src.rglob("*") if p.is_file()
    }
    original = {k: v for k, v in original.items() if k != "stray-root-file"}
    result = {
        str(p.relative_to(pulled)): p.read_bytes() for p in pulled.rglob("*") if p.is_file()
    }
    assert result == original


def test_specific_mapping_applied_in_tree(tmp_path):
    mappings = {"/home/alice/work/webshop": "/Users/alice/projects/webshop"}
    src = make_projects(tmp_path)
    dest = tmp_path / "staging"
    remap_tree(src, dest, PathMapper.for_push(LOCAL, REMOTE, mappings))
    webshop = dest / "-Users-alice-projects-webshop"
    assert webshop.is_dir()
    session = (webshop / "session-1.jsonl").read_text(encoding="utf-8")
    assert "/Users/alice/projects/webshop" in session
