"""filter 白名单与 limit 契约：不同指标允许的过滤字段不同。"""
from __future__ import annotations

import pytest

from src.config import AppError
from src.plan.schema import QueryPlan
from src.semantic import compiler, registry


def test_repeat_rate_rejects_filter() -> None:
    plan = QueryPlan(metrics=["repeat_rate"], filters=[{"field": "state", "op": "=", "value": "SP"}])
    with pytest.raises(AppError) as exc:
        registry.validate_plan(plan)
    assert exc.value.code == "FILTER_NOT_ALLOWED"


def test_series_rejects_filter() -> None:
    plan = QueryPlan(metrics=["gmv_mom"], filters=[{"field": "state", "op": "=", "value": "SP"}])
    with pytest.raises(AppError) as exc:
        registry.validate_plan(plan)
    assert exc.value.code == "FILTER_NOT_ALLOWED"


def test_topn_allows_state_filter_but_not_state_dimension() -> None:
    plan = QueryPlan(metrics=["category_revenue"], filters=[{"field": "state", "op": "=", "value": "SP"}])
    registry.validate_plan(plan)
    compiled = compiler.compile_plan(plan)
    assert "JOIN customers c" in compiled.sql

    bad = QueryPlan(metrics=["category_revenue"], dimensions=["state"])
    with pytest.raises(AppError) as exc:
        registry.validate_plan(bad)
    assert exc.value.code == "DIMENSION_NOT_ALLOWED"


def test_grouped_metric_allows_state_filter() -> None:
    plan = QueryPlan(metrics=["gmv"], filters=[{"field": "state", "op": "=", "value": "SP"}])
    registry.validate_plan(plan)
    assert "JOIN customers c" in compiler.compile_plan(plan).sql


def test_compiler_also_rejects_forbidden_filter() -> None:
    plan = QueryPlan(metrics=["repeat_rate"], filters=[{"field": "state", "op": "=", "value": "SP"}])
    with pytest.raises(AppError) as exc:
        compiler.compile_plan(plan)
    assert exc.value.code == "FILTER_NOT_ALLOWED"


def test_topn_default_limit_is_ten() -> None:
    for metric_id in ("category_revenue", "seller_revenue"):
        compiled = compiler.compile_plan(QueryPlan(metrics=[metric_id]))
        assert compiled.params[-1] == 10


def test_grouped_default_limit_is_hundred() -> None:
    compiled = compiler.compile_plan(QueryPlan(metrics=["gmv"]))
    assert compiled.params[-1] == 100


def test_explicit_limit_wins() -> None:
    compiled = compiler.compile_plan(QueryPlan(metrics=["seller_revenue"], limit=3))
    assert compiled.params[-1] == 3


def test_topn_has_deterministic_tiebreaker() -> None:
    compiled = compiler.compile_plan(QueryPlan(metrics=["seller_revenue"], limit=3))
    assert "ORDER BY metric_value DESC, dim_0 ASC" in compiled.sql

def test_month_filter_is_rejected_because_time_uses_time_range() -> None:
    plan = QueryPlan(metrics=["gmv"], filters=[{"field": "month", "op": "=", "value": "2018-08"}])
    with pytest.raises(AppError) as exc:
        registry.validate_plan(plan)
    assert exc.value.code == "FILTER_NOT_ALLOWED"

