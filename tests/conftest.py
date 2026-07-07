import json
import shutil
from pathlib import Path

import pytest

from claude_hop import sync
from claude_hop.config import Config, Remote
from claude_hop.remap import encode_path

requires_rsync = pytest.mark.skipif(
    shutil.which("rsync") is None, reason="rsync not installed"
)


@pytest.fixture
def homes(tmp_path):
    """A fake local home and a fake remote home (local-path mode)."""
    local_home = tmp_path / "home" / "tester"
    remote_home = tmp_path / "Users" / "tester"
    (local_home / ".claude" / "projects").mkdir(parents=True)
    remote_home.mkdir(parents=True)
    return local_home, remote_home


@pytest.fixture
def not_running(monkeypatch):
    """Pretend Claude Code is not running (the real check would see this session)."""
    monkeypatch.setattr(sync, "claude_running", lambda: False)


def make_session(home: Path, rel: str, name: str = "s1.jsonl", extra: str = "") -> Path:
    """Create a fake session for a project at <home>/<rel>."""
    project_path = f"{home}/{rel}"
    project_dir = home / ".claude" / "projects" / encode_path(project_path)
    project_dir.mkdir(parents=True, exist_ok=True)
    f = project_dir / name
    f.write_text(json.dumps({"cwd": project_path}) + "\n" + extra, encoding="utf-8")
    return f


def local_cfg(
    remote_home: Path, mappings: dict[str, str] | None = None, name: str = "default"
) -> Config:
    remote = Remote(name=name, host="", home=str(remote_home), mappings=mappings or {})
    return Config(remotes={name: remote})


def session_cwd(path: Path) -> str:
    return json.loads(path.read_text(encoding="utf-8").splitlines()[0])["cwd"]
