"""QueryPlan 的输入边界：日期、limit、filter 形状都必须提前拒绝。"""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from src.plan.schema import Filter, QueryPlan, TimeRange


@pytest.mark.parametrize(
    "payload",
    [
        {"start": "2018-01-01", "end": "2017-01-01"},
        {"start": "not-a-date", "end": "2018-01-01"},
        {"start": "2018-01-01", "end": "2018-13-01"},
        {"start": "2018-01-01", "granularity": "day"},
        {"start": "2018-01-01", "compare": "prev_period"},
    ],
)
def test_invalid_time_range_is_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        TimeRange.model_validate(payload)


def test_valid_time_range_with_timestamp_prefix() -> None:
    tr = TimeRange(start="2018-01-01T00:00:00", end="2018-01-31")
    assert tr.start == "2018-01-01T00:00:00"


@pytest.mark.parametrize(
    "payload",
    [
        {"field": "state", "op": "in", "value": []},
        {"field": "state", "op": "in", "value": [{"x": 1}]},
        {"field": "state", "op": "between", "value": [1]},
        {"field": "state", "op": "=", "value": [1, 2]},
        {"field": "state", "op": "=", "value": None},
    ],
)
def test_invalid_filter_value_is_rejected(payload: dict) -> None:
    with pytest.raises(ValidationError):
        Filter.model_validate(payload)


def test_limit_defaults_to_none() -> None:
    assert QueryPlan(metrics=["gmv"]).limit is None


def test_limit_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(metrics=["gmv"], limit=0)


def test_duplicate_dimensions_are_rejected() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(metrics=["gmv"], dimensions=["month", "month"])


def test_clarify_and_unsupported_are_exclusive() -> None:
    with pytest.raises(ValidationError):
        QueryPlan(clarify="x", unsupported_reason="y")

def test_state_dimension_examples_are_in_prompt_catalog() -> None:
    from src.plan.llm import _dimension_text

    assert "SP=圣保罗" in _dimension_text()

