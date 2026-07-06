from claude_hop.remap import encode_path


def test_basic_home_path():
    assert encode_path("/home/alice/work/webshop") == "-home-alice-work-webshop"


def test_mac_home_path():
    assert encode_path("/Users/alice/work/webshop") == "-Users-alice-work-webshop"


def test_case_and_digits_preserved():
    assert encode_path("/home/Alice2/Proj3") == "-home-Alice2-Proj3"


def test_dots_and_underscores_become_dashes():
    assert encode_path("/home/alice/my_app.v2") == "-home-alice-my-app-v2"


def test_every_nonalnum_char_is_one_dash():
    assert encode_path("/a/b c!d") == "-a-b-c-d"


def test_unicode_becomes_dashes():
    assert encode_path("/home/alice/héllo/δoc") == "-home-alice-h-llo--oc"


def test_root():
    assert encode_path("/") == "-"


def test_accepts_pathlib_path():
    from pathlib import Path

    assert encode_path(Path("/home/alice")) == "-home-alice"
