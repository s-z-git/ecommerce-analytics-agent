"""QueryPlan 的容错边界：只补 null 默认值，不放宽业务校验。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.plan.schema import QueryPlan


def test_unsupported_plan_tolerates_null_time_fields() -> None:
    # 真实 LLM 在"删掉所有订单"时就返回过这种形状：unsupported 正确，但时间字段全是 null
    plan = QueryPlan.model_validate({
        "metrics": None,
        "dimensions": None,
        "filters": None,
        "order_by": None,
        "limit": None,
        "time_range": {"start": None, "end": None, "granularity": None, "compare": None},
        "clarify": None,
        "unsupported_reason": "不能删除数据",
    })
    assert plan.unsupported_reason == "不能删除数据"
    assert plan.time_range is not None and plan.time_range.granularity == "month"
    assert plan.metrics == [] and plan.limit is None


def test_clarify_plan_tolerates_null_fields() -> None:
    plan = QueryPlan.model_validate({"metrics": [], "clarify": "你指的是 GMV 还是回款？", "time_range": None})
    assert plan.clarify and plan.metrics == []


@pytest.mark.parametrize(
    "payload",
    [
        {"metrics": ["gmv"], "clarify": "同时给了澄清"},
        {"metrics": [], "clarify": None, "unsupported_reason": None},
    ],
)
def test_business_rules_still_reject_bad_plans(payload: dict) -> None:
    with pytest.raises(ValidationError):
        QueryPlan.model_validate(payload)
