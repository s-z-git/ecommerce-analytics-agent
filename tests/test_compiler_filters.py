"""编译器：过滤字段引用的 JOIN 必须一起渲染，否则 SQL 引用不存在的表。"""
from __future__ import annotations

import pytest

from src.config import DB_PATH
from src.exec import duck, guard
from src.plan.schema import QueryPlan
from src.semantic import compiler

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="需要先跑 scripts/build_db.py")


def _run(plan: QueryPlan):
    c = compiler.compile_plan(plan)
    return c, duck.run_sql(guard.check_sql(c.sql), c.params)


def test_state_filter_joins_customers() -> None:
    plan = QueryPlan.model_validate({
        "metrics": ["gmv"],
        "filters": [{"field": "state", "op": "=", "value": "SP"}],
        "time_range": {"start": "2018-01-01", "end": "2018-12-31"},
    })
    compiled, qr = _run(plan)
    assert "JOIN customers c ON c.customer_id = o.customer_id" in compiled.sql
    assert len(qr.rows) == 1


def test_state_filter_matches_gold() -> None:
    plan = QueryPlan.model_validate({
        "metrics": ["gmv"],
        "filters": [{"field": "state", "op": "=", "value": "SP"}],
        "time_range": {"start": "2018-01-01", "end": "2018-12-31"},
    })
    _compiled, qr = _run(plan)
    gold = """SELECT SUM(oi.price) FROM orders o
        JOIN order_items oi ON oi.order_id = o.order_id
        JOIN customers c ON c.customer_id = o.customer_id
        WHERE o.order_status NOT IN ('canceled','unavailable')
          AND c.customer_state = 'SP'
          AND o.order_purchase_timestamp >= '2018-01-01' AND o.order_purchase_timestamp < '2019-01-01'"""
    expected = duck.run_sql(gold, []).rows
    assert round(qr.rows[0][0], 2) == round(expected[0][0], 2)


def test_join_not_duplicated_when_state_also_grouped() -> None:
    plan = QueryPlan.model_validate({
        "metrics": ["orders"],
        "dimensions": ["state"],
        "filters": [{"field": "state", "op": "in", "value": ["SP", "RJ"]}],
        "time_range": {"start": "2018-01-01", "end": "2018-12-31"},
    })
    compiled, qr = _run(plan)
    assert compiled.sql.count("JOIN customers c") == 1
    assert len(qr.rows) == 2
