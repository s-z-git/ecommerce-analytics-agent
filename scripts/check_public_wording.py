"""Check that the public repository contains no internal project wording.

The pre-push hook calls this script only when pushing to the public remote.
Scope: README/DESIGN/CHANGELOG, docs, eval code, source, tests and hooks.
Tabular data under data/ and eval/questions*.jsonl is excluded.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Unicode escapes keep the checker itself from matching a plain-text search.
KEYWORDS = (
    "\u7b80\u5386",              # resume
    "\u9762\u8bd5",              # interview
    "\u6295\u9012",              # job application
    "\u79cb\u62db",              # autumn recruiting
    "\u6625\u62db",              # spring recruiting
    "\u4efb\u52a1\u6e05\u5355",  # task list
    "\u4ea4\u63a5\u6587\u6863",  # handoff doc
    "\u6536\u53e3\u6e05\u5355",  # wrap-up list
    "\u8def\u7ebf\u56fe",        # roadmap
    "\u91cc\u7a0b\u7891",        # milestone
    "\u4e0b\u4e00\u6b65",        # next step
    "\u8ba1\u5212\u6027",        # planning-related
    "\u4e0a\u573a",              # stage/interview wording
)
PATTERN = re.compile("|".join(KEYWORDS))

SCOPE_PREFIXES = (".githooks/", "docs/", "eval/", "src/", "scripts/", "tests/", "templates/")
SCOPE_FILES = ("README.md", "CHANGELOG.md", "DESIGN.md")
SELF = "scripts/check_public_wording.py"
DATA_SUFFIXES = (".jsonl", ".json", ".csv", ".png", ".jpg", ".jpeg", ".parquet", ".duckdb")


def in_scope(path: str) -> bool:
    """Return True for narrative docs and code, not data files."""
    if path == SELF:
        return False
    if path.endswith(DATA_SUFFIXES):
        return False
    if path in SCOPE_FILES:
        return True
    if path.startswith(SCOPE_PREFIXES):
        return True
    # Top-level entry scripts.
    return path.endswith(".py") and "/" not in path


def main() -> int:
    """Return 0 when clean; print findings and return 1 otherwise."""
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True,
        text=True, encoding="utf-8", check=True,
    ).stdout.splitlines()
    hits = []
    for name in files:
        if not in_scope(name):
            continue
        try:
            text = (ROOT / name).read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        for no, line in enumerate(text.splitlines(), 1):
            if PATTERN.search(line):
                hits.append(name + ":" + str(no) + ": " + line.strip())
    if hits:
        print("[public-wording] disallowed wording found:")
        for hit in hits:
            print("  " + hit)
        print("[public-wording] rewrite or remove it before pushing")
        return 1
    print("[public-wording] ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
