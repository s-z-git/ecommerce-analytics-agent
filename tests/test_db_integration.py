"""真实 DuckDB 集成测试：编译器 SQL + 执行层，不用 monkeypatch 掉数据库。

和现有测试的区别：这里真的建库、真的执行 SQL，而不是只校验字符串或 mock。
fixture 只建 orders / order_items 两张表，够 gmv / orders / cancel_rate 三条指标用。
"""
from __future__ import annotations

import duckdb
import pytest

from src.config import AppError
from src.exec import duck
from src.plan.schema import QueryPlan, TimeRange
from src.semantic.compiler import compile_plan


def _build_mini_db(path):
    con = duckdb.connect(str(path))
    con.execute("CREATE TABLE orders("
                "order_id VARCHAR, order_status VARCHAR, order_purchase_timestamp TIMESTAMP)")
    con.execute("CREATE TABLE order_items("
                "order_id VARCHAR, order_item_id INTEGER, product_id VARCHAR, "
                "seller_id VARCHAR, price DOUBLE, freight_value DOUBLE)")
    con.executemany("INSERT INTO orders VALUES (?, ?, ?)", [
        ("o1", "delivered", "2018-01-05 10:00:00"),
        ("o2", "delivered", "2018-01-10 10:00:00"),
        ("o3", "canceled", "2018-01-15 10:00:00"),
        ("o4", "delivered", "2018-02-01 10:00:00"),
    ])
    con.executemany("INSERT INTO order_items VALUES (?, ?, ?, ?, ?, ?)", [
        ("o1", 1, "p1", "s1", 100.0, 0.0),
        ("o1", 2, "p2", "s1", 50.0, 5.0),
        ("o2", 1, "p1", "s2", 200.0, 0.0),
        ("o3", 1, "p3", "s3", 999.0, 0.0),
        ("o4", 1, "p1", "s1", 400.0, 0.0),
    ])
    con.close()


@pytest.fixture()
def mini_db(tmp_path, monkeypatch):
    db = tmp_path / "mini.duckdb"
    _build_mini_db(db)
    # run_sql 里的 _connect 读模块级 DB_PATH，换到临时库
    monkeypatch.setattr(duck, "DB_PATH", db)
    return db


def _jan_plan(metric: str) -> QueryPlan:
    return QueryPlan(
        metrics=[metric],
        dimensions=["month"],
        time_range=TimeRange(start="2018-01-01", end="2018-01-31"),
    )


def _run(plan: QueryPlan):
    compiled = compile_plan(plan)
    return duck.run_sql(compiled.sql, compiled.params)


def test_gmv_by_month_on_real_duckdb(mini_db):
    result = _run(_jan_plan("gmv"))
    assert result.rows == [("2018-01", 350.0)]


def test_orders_excludes_canceled_on_real_duckdb(mini_db):
    result = _run(_jan_plan("orders"))
    assert result.rows == [("2018-01", 2)]


def test_cancel_rate_denominator_on_real_duckdb(mini_db):
    result = _run(_jan_plan("cancel_rate"))
    assert result.rows[0][0] == "2018-01"
    assert result.rows[0][1] == pytest.approx(1 / 3, rel=1e-6)


def test_db_not_found_gives_app_error(tmp_path, monkeypatch):
    monkeypatch.setattr(duck, "DB_PATH", tmp_path / "missing.duckdb")
    compiled = compile_plan(_jan_plan("orders"))
    with pytest.raises(AppError) as exc:
        duck.run_sql(compiled.sql, compiled.params)
    assert "DB_NOT_FOUND" in str(exc.value) or "数据库不存在" in str(exc.value)

