"""相对路径必须按项目根目录解析，不能跟着启动 cwd 跑。"""
from __future__ import annotations

import os
from pathlib import Path

from src.config import ROOT, _resolve_path


def test_relative_path_is_anchored_to_root(monkeypatch) -> None:
    monkeypatch.setenv("DB_PATH", "data/warehouse/test.duckdb")
    assert _resolve_path("DB_PATH", Path("unused")) == ROOT / "data" / "warehouse" / "test.duckdb"


def test_absolute_path_is_preserved(monkeypatch) -> None:
    abs_path = Path("F:/tmp/test.duckdb") if os.name == "nt" else Path("/tmp/test.duckdb")
    monkeypatch.setenv("DB_PATH", str(abs_path))
    assert _resolve_path("DB_PATH", Path("unused")) == abs_path
