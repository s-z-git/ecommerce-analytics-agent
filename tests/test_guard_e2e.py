"""端到端注入测试：假设 LLM 已经沦陷，只允许它输出 QueryPlan。

与 test_security.py 的区别：
- 那里直接调 guard.check_sql()，测的是最后一道门；
- 这里用 pipeline.answer(plan_override=...)，从 registry 校验一路走到 DuckDB。

filter value 注入不会被当成 SQL，而是被编译成 `?` 占位符；
所以它的安全结果是 no_data（合法 SELECT + 0 行），不是 error。
"""
from __future__ import annotations

from typing import Any

import pytest

from src.agent import pipeline
from src.config import DB_PATH
from src.plan.schema import Filter, QueryPlan, TimeRange

requires_db = pytest.mark.skipif(not DB_PATH.exists(), reason='DB not built; run scripts/build_db.py')

INJECTION = "1'; DROP TABLE orders; --"


def _run_with_captured_trace(monkeypatch, plan: QueryPlan) -> tuple[dict[str, Any], dict[str, Any]]:
    captured: list[dict[str, Any]] = []
    monkeypatch.setattr(pipeline, "_trace", lambda record: captured.append(record))
    result = pipeline.answer("e2e injection test", plan_override=plan)
    assert captured, "pipeline 必须写一条 trace"
    return result, captured[-1]


def _assert_no_success(result: dict[str, Any], trace: dict[str, Any]) -> None:
    assert result.get("status") != "ok"
    assert result.get("rows") in (None, [])
    assert result.get("row_count") in (None, 0)
    assert trace.get("error_detail") is None
    sql = result.get("sql")
    if sql:
        # 走到 SQL 的只有参数绑定场景：仍必须是单条 SELECT/CTE。
        assert sql.lstrip().upper().startswith(("SELECT", "WITH"))
        assert "DROP" not in sql.upper()
        assert "?" in sql


def test_queryplan_has_no_sql_field() -> None:
    """结构性约束：模型永远没有地方放一条 SQL。"""
    fields = set(QueryPlan.model_fields)
    assert not {name for name in fields if "sql" in name.lower()}
    assert not (fields & {"query", "statement", "raw_query", "raw_sql"})


@requires_db
def test_e2e_rejects_unknown_metric(monkeypatch) -> None:
    plan = QueryPlan(metrics=["drop table orders"])
    result, trace = _run_with_captured_trace(monkeypatch, plan)
    _assert_no_success(result, trace)
    assert result["status"] == "error"
    assert result["error_code"] == "UNKNOWN_METRIC"
    assert result.get("sql") is None


@requires_db
def test_e2e_rejects_dimension_injection(monkeypatch) -> None:
    plan = QueryPlan(metrics=["gmv"], dimensions=["state; DROP TABLE orders"])
    result, trace = _run_with_captured_trace(monkeypatch, plan)
    _assert_no_success(result, trace)
    assert result["status"] == "error"
    assert result["error_code"] == "UNKNOWN_DIMENSION"
    assert result.get("sql") is None


@requires_db
def test_e2e_binds_filter_value_injection(monkeypatch) -> None:
    """最坏 QueryPlan 也不是拼接 SQL；注入值被绑定后只得到 no_data。"""
    plan = QueryPlan(
        metrics=["gmv"],
        filters=[Filter(field="state", op="=", value=INJECTION)],
    )
    result, trace = _run_with_captured_trace(monkeypatch, plan)
    _assert_no_success(result, trace)
    assert result["status"] == "no_data"
    assert result["rows"] == []
    assert INJECTION not in result["sql"]


@requires_db
def test_e2e_rejects_out_of_range_time(monkeypatch) -> None:
    plan = QueryPlan(
        metrics=["gmv"],
        time_range=TimeRange(start="2019-01-01", end="2019-12-31"),
    )
    result, trace = _run_with_captured_trace(monkeypatch, plan)
    _assert_no_success(result, trace)
    assert result["status"] == "unsupported"
    assert "超出数据覆盖区间" in result["message"]
    assert result.get("sql") is None
