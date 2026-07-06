import pytest

from claude_hop.remap import PathMapper

LOCAL = "/home/alice"
REMOTE = "/Users/alice"


def push(mappings=None):
    return PathMapper.for_push(LOCAL, REMOTE, mappings)


def pull(mappings=None):
    return PathMapper.for_pull(LOCAL, REMOTE, mappings)


# ------------------------------------------------------------- home fallback


def test_home_remap_in_text():
    line = '{"cwd":"/home/alice/work/webshop","file":"/home/alice/notes.md"}'
    assert (
        push().remap_text(line)
        == '{"cwd":"/Users/alice/work/webshop","file":"/Users/alice/notes.md"}'
    )


def test_home_remap_dirname():
    assert push().remap_dirname("-home-alice-work-webshop") == "-Users-alice-work-webshop"


def test_bare_home_dirname():
    assert push().remap_dirname("-home-alice") == "-Users-alice"


def test_unrelated_dirname_unchanged():
    assert push().remap_dirname("-tmp-scratch") == "-tmp-scratch"


def test_unrelated_text_unchanged():
    line = '{"cwd":"/opt/data"}'
    assert push().remap_text(line) == line


# ------------------------------------------------------- specific mappings


def test_specific_mapping_beats_home_fallback():
    m = push({"/home/alice/work/webshop": "/Users/alice/projects/webshop"})
    assert (
        m.remap_text('{"cwd":"/home/alice/work/webshop/src"}')
        == '{"cwd":"/Users/alice/projects/webshop/src"}'
    )
    # everything else still gets the generic home remap
    assert m.remap_text('{"f":"/home/alice/other"}') == '{"f":"/Users/alice/other"}'


def test_specific_mapping_beats_home_in_dirnames():
    m = push({"/home/alice/work/webshop": "/Users/alice/projects/webshop"})
    assert m.remap_dirname("-home-alice-work-webshop") == "-Users-alice-projects-webshop"
    assert m.remap_dirname("-home-alice-work-other") == "-Users-alice-work-other"


def test_longest_source_wins_between_mappings():
    m = push(
        {
            "/home/alice/work": "/Users/alice/w",
            "/home/alice/work/webshop": "/Users/alice/projects/webshop",
        }
    )
    assert m.remap_text("/home/alice/work/webshop/x") == "/Users/alice/projects/webshop/x"
    assert m.remap_text("/home/alice/work/beta/x") == "/Users/alice/w/beta/x"


def test_trailing_slashes_normalized():
    m = push({"/home/alice/work/webshop/": "/Users/alice/projects/webshop/"})
    assert m.remap_text("/home/alice/work/webshop/x") == "/Users/alice/projects/webshop/x"


# ------------------------------------------------------------- boundaries


def test_home_prefix_of_other_username_not_rewritten():
    # /home/al is a strict prefix of /home/alice
    m = PathMapper.for_push("/home/al", "/Users/al")
    assert m.remap_text('{"cwd":"/home/al/x"}') == '{"cwd":"/Users/al/x"}'
    assert m.remap_text('{"cwd":"/home/alice/x"}') == '{"cwd":"/home/alice/x"}'
    assert m.remap_dirname("-home-al-x") == "-Users-al-x"
    assert m.remap_dirname("-home-alice-x") == "-home-alice-x"


def test_dot_dash_underscore_suffixes_not_rewritten():
    for other in ("/home/alice.bak/f", "/home/alice-old/f", "/home/alice_2/f"):
        assert push().remap_text(other) == other


def test_path_embedded_in_longer_path_not_rewritten():
    assert push().remap_text('{"f":"/backup/home/alice/x"}') == '{"f":"/backup/home/alice/x"}'


def test_encoded_name_not_rewritten_mid_string():
    # a project at /backup/home/alice/x contains the encoded home mid-name
    assert push().remap_dirname("-backup-home-alice-x") == "-backup-home-alice-x"


def test_path_at_end_of_text():
    assert push().remap_text("cwd is /home/alice") == "cwd is /Users/alice"


def test_path_before_json_escape():
    # inside raw JSONL a newline in a string is backslash-n: path ends at the backslash
    assert push().remap_text('"output":"/home/alice\\nnext"') == '"output":"/Users/alice\\nnext"'


# ------------------------------------------------- single-pass, no chaining


def test_no_chained_replacement():
    # sequential str.replace would rewrite a -> b, then b -> c; a single
    # pass must stop at b
    m = PathMapper.for_push(
        "/home/alice",
        "/home/alice",
        {"/home/alice/a": "/home/alice/b", "/home/alice/b": "/home/alice/c"},
    )
    assert m.remap_text("/home/alice/a") == "/home/alice/b"
    assert m.remap_text("/home/alice/b") == "/home/alice/c"


def test_mapping_target_containing_local_home_not_rewritten_again():
    m = push({"/home/alice/work/webshop": "/Users/alice/dev/webshop"})
    out = m.remap_text("/home/alice/work/webshop and /home/alice/misc")
    assert out == "/Users/alice/dev/webshop and /Users/alice/misc"


# --------------------------------------------------------------- round trip


ROUND_TRIP_SAMPLES = [
    '{"type":"user","cwd":"/home/alice/work/webshop","v":1}\n',
    '{"paths":["/home/alice/a.py","/home/alice/work/webshop/b.py"]}\n',
    "this line is {not valid json but mentions /home/alice/x\n",
    '{"unicode":"héllo — /home/alice/δoc/χ.txt"}\n',
    '{"untouched":"/opt/other/home/alice-lookalike"}\n',
]


def test_text_round_trip_is_identity():
    mappings = {"/home/alice/work/webshop": "/Users/alice/projects/webshop"}
    fwd, back = push(mappings), pull(mappings)
    for sample in ROUND_TRIP_SAMPLES:
        assert back.remap_text(fwd.remap_text(sample)) == sample


def test_dirname_round_trip_is_identity():
    mappings = {"/home/alice/work/webshop": "/Users/alice/projects/webshop"}
    fwd, back = push(mappings), pull(mappings)
    for name in (
        "-home-alice-work-webshop",
        "-home-alice-work-other",
        "-home-alice",
        "-tmp-scratch",
    ):
        assert back.remap_dirname(fwd.remap_dirname(name)) == name


def test_inverted_equals_for_pull():
    mappings = {"/home/alice/work/webshop": "/Users/alice/projects/webshop"}
    assert push(mappings).inverted().pairs == pull(mappings).pairs


# --------------------------------------------------------------- validation


def test_relative_source_rejected():
    with pytest.raises(ValueError, match="absolute"):
        PathMapper([("work/webshop", "/Users/alice/webshop")])


def test_relative_target_rejected():
    with pytest.raises(ValueError, match="absolute"):
        PathMapper([("/home/alice/webshop", "webshop")])


def test_root_rejected():
    with pytest.raises(ValueError, match="root"):
        PathMapper.for_push("/", "/Users/alice")


def test_duplicate_source_with_different_targets_rejected():
    # a mapping whose source is the local home collides with the home pair
    with pytest.raises(ValueError, match="ambiguous"):
        PathMapper.for_push(LOCAL, REMOTE, {LOCAL: "/Users/alice/elsewhere"})


def test_encoded_collision_rejected():
    # distinct raw paths that encode identically cannot map to different targets
    with pytest.raises(ValueError, match="ambiguous"):
        PathMapper.for_push(
            LOCAL,
            REMOTE,
            {"/home/alice/a.b": "/Users/alice/x", "/home/alice/a-b": "/Users/alice/y"},
        )


def test_empty_mapper_is_identity():
    m = PathMapper([])
    assert m.remap_text("/home/alice/x") == "/home/alice/x"
    assert m.remap_dirname("-home-alice-x") == "-home-alice-x"


def test_identity_home_pair_is_harmless():
    m = PathMapper.for_push("/home/alice", "/home/alice")
    assert m.remap_text('{"cwd":"/home/alice/x"}') == '{"cwd":"/home/alice/x"}'
