"""语义层二次校验：空 time_range 不等于"按时间过滤"。"""
from __future__ import annotations

import pytest

from src.config import AppError
from src.plan.schema import QueryPlan
from src.semantic import registry


def test_empty_time_range_is_not_a_time_filter() -> None:
    # LLM 常给没有时间字段的指标补一个 start/end 都为 null 的 time_range
    plan = QueryPlan.model_validate({
        "metrics": ["repeat_rate"],
        "time_range": {"start": None, "end": None, "granularity": "month", "compare": "none"},
    })
    metric, _dims = registry.validate_plan(plan)
    assert metric["label"] == "复购率"


def test_real_time_filter_on_customer_metric_is_rejected() -> None:
    plan = QueryPlan.model_validate({
        "metrics": ["repeat_rate"],
        "time_range": {"start": "2018-01-01", "end": "2018-06-30"},
    })
    with pytest.raises(AppError) as excinfo:
        registry.validate_plan(plan)
    assert excinfo.value.code == "TIME_NOT_SUPPORTED"

def test_single_month_plan_drops_redundant_month_dimension() -> None:
    from src.semantic import registry

    plan = QueryPlan.model_validate({
        "metrics": ["aov"],
        "dimensions": ["month"],
        "time_range": {"start": "2018-04-01", "end": "2018-04-30"},
        "order_by": [{"field": "month", "desc": False}],
    })
    fixed = registry.canonicalize(plan)
    assert fixed.dimensions == []
    assert fixed.order_by == []


def test_multi_month_plan_keeps_month_dimension() -> None:
    from src.semantic import registry

    plan = QueryPlan.model_validate({
        "metrics": ["orders"],
        "dimensions": ["month"],
        "time_range": {"start": "2018-01-01", "end": "2018-06-30"},
    })
    assert registry.canonicalize(plan).dimensions == ["month"]


def test_plan_without_time_bounds_is_left_alone() -> None:
    from src.semantic import registry

    plan = QueryPlan.model_validate({"metrics": ["orders"], "dimensions": ["month"]})
    assert registry.canonicalize(plan).dimensions == ["month"]

def test_full_range_filter_on_no_time_metric_is_dropped() -> None:
    from src.semantic import registry

    plan = QueryPlan.model_validate({
        "metrics": ["repeat_rate"],
        "time_range": {"start": "2016-09-04", "end": "2018-10-17"},
    })
    fixed = registry.canonicalize(plan, ("2016-09-04", "2018-10-17"))
    assert fixed.time_range is None


def test_partial_range_on_no_time_metric_is_kept() -> None:
    from src.semantic import registry

    plan = QueryPlan.model_validate({
        "metrics": ["repeat_rate"],
        "time_range": {"start": "2018-01-01", "end": "2018-06-30"},
    })
    assert registry.canonicalize(plan, ("2016-09-04", "2018-10-17")).time_range is not None


def test_full_range_filter_on_time_metric_is_kept() -> None:
    from src.semantic import registry

    # 有时间字段的指标不做这层规范化，避免规则扩散到正常查询
    plan = QueryPlan.model_validate({
        "metrics": ["gmv"],
        "time_range": {"start": "2016-09-04", "end": "2018-10-17"},
    })
    assert registry.canonicalize(plan, ("2016-09-04", "2018-10-17")).time_range is not None

def test_dimension_pinned_by_equality_filter_is_dropped() -> None:
    from src.semantic import registry

    plan = QueryPlan.model_validate({
        "metrics": ["gmv"],
        "dimensions": ["state"],
        "filters": [{"field": "state", "op": "=", "value": "SP"}],
    })
    assert registry.canonicalize(plan).dimensions == []


def test_multi_value_in_filter_keeps_dimension() -> None:
    from src.semantic import registry

    plan = QueryPlan.model_validate({
        "metrics": ["orders"],
        "dimensions": ["state"],
        "filters": [{"field": "state", "op": "in", "value": ["RJ", "MG"]}],
    })
    assert registry.canonicalize(plan).dimensions == ["state"]


def test_single_value_in_filter_drops_dimension() -> None:
    from src.semantic import registry

    plan = QueryPlan.model_validate({
        "metrics": ["orders"],
        "dimensions": ["state"],
        "filters": [{"field": "state", "op": "in", "value": ["SP"]}],
    })
    assert registry.canonicalize(plan).dimensions == []

def test_time_filter_on_customer_metric_asks_for_clarification() -> None:
    from src.semantic import registry

    plan = QueryPlan.model_validate({
        "metrics": ["repeat_rate"],
        "time_range": {"start": "2018-01-01", "end": "2018-06-30"},
    })
    msg = registry.time_filter_clarify(plan)
    assert msg and "全量" in msg


def test_time_filter_clarify_ignores_metrics_with_time_field() -> None:
    from src.semantic import registry

    plan = QueryPlan.model_validate({
        "metrics": ["gmv"],
        "time_range": {"start": "2018-01-01", "end": "2018-06-30"},
    })
    assert registry.time_filter_clarify(plan) is None


def test_pipeline_returns_clarify_not_error(monkeypatch) -> None:
    from src.agent import pipeline

    plan = QueryPlan.model_validate({
        "metrics": ["repeat_rate"],
        "time_range": {"start": "2018-01-01", "end": "2018-06-30"},
    })
    monkeypatch.setattr(pipeline.llm, "parse_plan", lambda question, data_range: (plan, None))
    monkeypatch.setattr(pipeline, "_data_range", lambda: ("2016-09-04", "2018-10-17"))
    monkeypatch.setattr(pipeline, "_trace", lambda record: None)

    result = pipeline.answer("2018 年复购率是多少")
    assert result["status"] == "clarify"
    assert "全量" in result["message"]
