"""品类码 → 中文名映射（B4a）。

两条断言：
1. 映射表键集合 == 库里非空品类码集合（漏一个就静默显示英文码）；
2. 展示层翻译生效，且**不修改入参**（原始码要留给导出/追溯）。
"""
from __future__ import annotations

import duckdb
import pytest

from src.config import DB_PATH
from src.semantic import registry
from src.ui import summary


def _db_codes() -> set[str]:
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        rows = con.execute(
            "SELECT DISTINCT product_category_name FROM products "
            "WHERE product_category_name IS NOT NULL"
        ).fetchall()
    finally:
        con.close()
    return {row[0] for row in rows}


def test_category_zh_covers_every_code() -> None:
    if not DB_PATH.exists():
        pytest.skip("DB not built; run scripts/build_db.py")
    codes = _db_codes()
    assert codes, "库里没有品类码，测试前提不成立"
    mapping = registry.get_category_zh()
    assert set(mapping) == codes, (
        f"缺: {sorted(codes - set(mapping))}；多: {sorted(set(mapping) - codes)}"
    )


def test_category_zh_values_are_unique_and_chinese() -> None:
    mapping = registry.get_category_zh()
    assert len(set(mapping.values())) == len(mapping), "中文名重复会让两个品类看起来一样"
    for code, zh in mapping.items():
        assert zh.strip(), f"{code} 的中文名是空的"
        assert any("\u4e00" <= ch <= "\u9fff" for ch in zh), f"{code} -> {zh} 不含汉字"


def test_summary_localizes_category_rows() -> None:
    result = {
        "status": "ok",
        "rows": [["beleza_saude", 544009.42], ["relogios_presentes", 539816.30]],
        "columns": ["品类", "收入"],
        "metric": {"id": "category_revenue", "unit": "BRL", "label": "品类收入"},
        "row_count": 2,
    }
    text = summary.summarize(result)
    assert "美妆健康" in text
    assert "beleza_saude" not in text
    # 原始行不能被改：导出、trace 用的是原始码
    assert result["rows"][0][0] == "beleza_saude"


def test_summary_leaves_non_category_metrics_alone() -> None:
    result = {
        "status": "ok",
        "rows": [["SP", 100.0]],
        "columns": ["州", "GMV"],
        "metric": {"id": "gmv", "unit": "BRL", "label": "GMV"},
        "row_count": 1,
    }
    text = summary.summarize(result)
    assert "SP" in text


def test_summary_localizes_category_when_column_order_shuffled() -> None:
    result = {
        "status": "ok",
        "rows": [[544009.42, "beleza_saude"]],
        "columns": ["收入", "品类"],
        "metric": {"id": "category_revenue", "unit": "BRL", "label": "品类收入"},
        "row_count": 1,
    }
    rows = summary._display_rows(result, result["rows"], result["columns"])
    assert rows[0][1] == "美妆健康"
    # 原始行不能被改：导出、trace 用的是原始码
    assert result["rows"][0][1] == "beleza_saude"


def test_summary_returns_rows_when_category_column_missing() -> None:
    result = {
        "status": "ok",
        "rows": [["SP", 100.0]],
        "columns": ["州", "GMV"],
        "metric": {"id": "category_revenue", "unit": "BRL", "label": "品类收入"},
        "row_count": 1,
    }
    rows = summary._display_rows(result, result["rows"], result["columns"])
    assert rows == [["SP", 100.0]]
    assert result["rows"] == [["SP", 100.0]]
