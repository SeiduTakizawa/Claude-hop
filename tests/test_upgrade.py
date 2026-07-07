import io
import json
import shutil
import sys
import urllib.request

from claude_hop import upgrade


class FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_latest_version_parses_pypi(monkeypatch):
    payload = json.dumps({"info": {"version": "9.9.9"}}).encode()
    monkeypatch.setattr(
        urllib.request, "urlopen", lambda url, timeout=0: FakeResponse(payload)
    )
    assert upgrade.latest_version() == "9.9.9"


def test_latest_version_swallows_network_errors(monkeypatch):
    def boom(url, timeout=0):
        raise OSError("no network")

    monkeypatch.setattr(urllib.request, "urlopen", boom)
    assert upgrade.latest_version() is None


def test_dev_checkout_detected():
    # the test suite itself runs from the git checkout
    root = upgrade.dev_checkout_root()
    assert root is not None
    assert (root / "pyproject.toml").exists()


def test_detect_refuses_dev_checkout():
    assert upgrade.detect_upgrade_command() is None


def test_detect_uv_tool_install(monkeypatch, tmp_path):
    monkeypatch.setattr(upgrade, "dev_checkout_root", lambda: None)
    monkeypatch.setattr(
        sys, "prefix", str(tmp_path / "share" / "uv" / "tools" / "claude-hop")
    )
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert upgrade.detect_upgrade_command() == ["uv", "tool", "upgrade", "claude-hop"]


def test_detect_pipx_install(monkeypatch, tmp_path):
    monkeypatch.setattr(upgrade, "dev_checkout_root", lambda: None)
    monkeypatch.setattr(
        sys, "prefix", str(tmp_path / "pipx" / "venvs" / "claude-hop")
    )
    monkeypatch.setattr(shutil, "which", lambda name: f"/usr/bin/{name}")
    assert upgrade.detect_upgrade_command() == ["pipx", "upgrade", "claude-hop"]


def test_detect_falls_back_to_pip(monkeypatch, tmp_path):
    import importlib.util

    monkeypatch.setattr(upgrade, "dev_checkout_root", lambda: None)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    assert upgrade.detect_upgrade_command() == [
        sys.executable,
        "-m",
        "pip",
        "install",
        "--upgrade",
        "claude-hop",
    ]


def test_detect_returns_none_without_pip(monkeypatch, tmp_path):
    import importlib.util

    monkeypatch.setattr(upgrade, "dev_checkout_root", lambda: None)
    monkeypatch.setattr(sys, "prefix", str(tmp_path / "venv"))
    monkeypatch.setattr(shutil, "which", lambda name: None)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: None)
    assert upgrade.detect_upgrade_command() is None


def test_is_newer_orders_versions():
    assert upgrade.is_newer("0.2.0", "0.1.0")
    assert not upgrade.is_newer("0.1.0", "0.2.0")  # local ahead of PyPI
    assert not upgrade.is_newer("0.2.0", "0.2.0")
    assert upgrade.is_newer("0.10.0", "0.9.9")  # numeric, not lexicographic
    assert upgrade.is_newer("1.0.0", "dev")  # unparseable falls back to !=
