"""Pure path-remapping logic.

Claude Code stores sessions under ``~/.claude/projects/<encoded>/``, where
``<encoded>`` is the project's absolute path with every non-alphanumeric
character replaced by ``-`` (``/home/alice/work/webshop`` becomes
``-home-alice-work-webshop``). The same absolute paths also appear inside the
session JSONL files (``cwd`` fields, file paths in tool calls). Syncing
sessions between machines whose homes differ therefore requires rewriting
both the directory names and the file contents; this module implements that
rewrite as ordered, single-pass string substitution.
"""

from __future__ import annotations

import re
import shutil
from collections.abc import Iterable, Mapping
from pathlib import Path

_ENCODE_RE = re.compile(r"[^A-Za-z0-9]")

# Boundaries for matching a source path inside raw JSONL text. A match must
# not sit inside a longer path segment on either side: "/home/al" must match
# in '"cwd":"/home/al/x"' but not in "/home/alice/x" or "/backup/home/al/x".
# "/" is the segment separator, so it is a valid neighbour; word characters
# (unicode-aware), ".", and "-" would extend the segment, so they are not.
_RAW_PREFIX = r"(?<![\w.-])"
_RAW_SUFFIX = r"(?![\w.-])"

# Encoded directory names always start with the encoding of the leading "/",
# so a legitimate match can only sit at the start of the name. The suffix
# guard stops "-home-al" from matching inside "-home-alice-x" ("-" is the
# only separator left after encoding).
_ENC_PREFIX = r"\A"
_ENC_SUFFIX = r"(?![A-Za-z0-9])"


def encode_path(path: str | Path) -> str:
    """Encode an absolute path the way Claude Code names project dirs."""
    return _ENCODE_RE.sub("-", str(path))


def _normalize(path: str | Path, what: str) -> str:
    s = str(path)
    if not s.startswith("/"):
        raise ValueError(f"{what} must be an absolute path, got {s!r}")
    s = s.rstrip("/")
    if not s:
        raise ValueError(f"{what} must not be the filesystem root")
    return s


def _compile(
    pairs: Iterable[tuple[str, str]], prefix: str, suffix: str, space: str
) -> tuple[re.Pattern[str] | None, dict[str, str]]:
    table: dict[str, str] = {}
    for src, dst in pairs:
        if src in table and table[src] != dst:
            raise ValueError(
                f"ambiguous {space} mapping: {src!r} maps to both {table[src]!r} and {dst!r}"
            )
        table[src] = dst
    if not table:
        return None, table
    # Longest alternative first: regex alternation is ordered, so the most
    # specific mapping wins at any given position.
    alts = sorted(table, key=len, reverse=True)
    pattern = prefix + "(?:" + "|".join(map(re.escape, alts)) + ")" + suffix
    return re.compile(pattern), table


class PathMapper:
    """Ordered path replacements for raw text and encoded directory names.

    Substitution is a single regex pass: each position in the input is
    rewritten at most once, so the output of one mapping can never be
    re-matched by another (sequential ``str.replace`` chains would).
    """

    def __init__(self, pairs: Iterable[tuple[str | Path, str | Path]]):
        cleaned = [
            (_normalize(src, "mapping source"), _normalize(dst, "mapping target"))
            for src, dst in pairs
        ]
        cleaned.sort(key=lambda p: len(p[0]), reverse=True)
        self.pairs: tuple[tuple[str, str], ...] = tuple(cleaned)
        self._raw_re, self._raw_table = _compile(self.pairs, _RAW_PREFIX, _RAW_SUFFIX, "path")
        enc_pairs = [(encode_path(s), encode_path(d)) for s, d in self.pairs]
        self._enc_re, self._enc_table = _compile(enc_pairs, _ENC_PREFIX, _ENC_SUFFIX, "encoded")

    @classmethod
    def for_push(
        cls,
        local_home: str | Path,
        remote_home: str | Path,
        mappings: Mapping[str, str] | None = None,
    ) -> PathMapper:
        """Mapper for local → remote. Explicit ``mappings`` take priority over
        the generic home remap (longest-source-first ordering guarantees it)."""
        pairs: list[tuple[str | Path, str | Path]] = list((mappings or {}).items())
        pairs.append((local_home, remote_home))
        return cls(pairs)

    @classmethod
    def for_pull(
        cls,
        local_home: str | Path,
        remote_home: str | Path,
        mappings: Mapping[str, str] | None = None,
    ) -> PathMapper:
        """Mapper for remote → local: the push mapper with every pair swapped."""
        return cls.for_push(local_home, remote_home, mappings).inverted()

    def inverted(self) -> PathMapper:
        return PathMapper([(dst, src) for src, dst in self.pairs])

    def remap_text(self, text: str) -> str:
        """Rewrite every path occurrence in raw text (JSONL contents)."""
        if self._raw_re is None:
            return text
        return self._raw_re.sub(lambda m: self._raw_table[m.group(0)], text)

    def remap_dirname(self, name: str) -> str:
        """Rewrite an encoded project directory name."""
        if self._enc_re is None:
            return name
        return self._enc_re.sub(lambda m: self._enc_table[m.group(0)], name)


def remap_tree(src_root: Path, dest_root: Path, mapper: PathMapper) -> None:
    """Copy a projects tree into ``dest_root``, renaming each project
    directory and rewriting path strings inside every ``.jsonl`` file.

    ``src_root`` is never modified; mtimes are preserved so a later
    ``rsync -u`` merge keeps whichever side is newer.
    """
    dest_root.mkdir(parents=True, exist_ok=True)
    for proj in sorted(p for p in src_root.iterdir() if p.is_dir()):
        target = dest_root / mapper.remap_dirname(proj.name)
        target.mkdir(parents=True, exist_ok=True)
        for f in sorted(proj.rglob("*")):
            out = target / f.relative_to(proj)
            if f.is_dir():
                out.mkdir(parents=True, exist_ok=True)
            elif f.suffix == ".jsonl":
                remap_file(f, out, mapper)
            else:
                shutil.copy2(f, out)


def remap_file(src: Path, out: Path, mapper: PathMapper) -> None:
    """Copy one text file with path strings rewritten, mtime preserved."""
    data = src.read_bytes()
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        # Not valid UTF-8: copying verbatim beats corrupting it with
        # replacement characters. Its paths stay unmapped, but the file
        # survives the round trip byte-identical.
        shutil.copy2(src, out)
        return
    out.write_text(mapper.remap_text(text), encoding="utf-8")
    shutil.copystat(src, out)
