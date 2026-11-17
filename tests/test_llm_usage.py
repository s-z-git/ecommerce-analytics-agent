"""计划解析失败时的 token usage 也必须进 trace。"""
from __future__ import annotations

import pytest

from src.agent import pipeline
from src.config import AppError
from src.plan import llm


def test_parse_plan_attaches_usage_when_repair_fails(monkeypatch) -> None:
    monkeypatch.setattr(llm, "DEEPSEEK_API_KEY", "test")

    class DummyOpenAI:
        def __init__(self, **kwargs):
            pass

    monkeypatch.setattr(llm, "OpenAI", DummyOpenAI)

    def fake_complete(client, messages):
        return "{}", {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}

    monkeypatch.setattr(llm, "_complete", fake_complete)

    with pytest.raises(AppError) as excinfo:
        llm.parse_plan("q", ("2016-09-04", "2018-10-17"))

    assert excinfo.value.code == "BAD_LLM_PLAN"
    assert excinfo.value.usage == {
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "total_tokens": 30,
    }


def test_pipeline_trace_records_usage_when_parse_plan_raises(monkeypatch) -> None:
    captured = []

    def fake_parse_plan(question, data_range):
        raise AppError(
            "BAD_LLM_PLAN",
            "bad plan",
            usage={"prompt_tokens": 7, "completion_tokens": 3, "total_tokens": 10},
        )

    monkeypatch.setattr(pipeline, "_data_range", lambda: ("2016-09-04", "2018-10-17"))
    monkeypatch.setattr(pipeline.llm, "parse_plan", fake_parse_plan)
    monkeypatch.setattr(pipeline, "_trace", lambda record: captured.append(record))

    result = pipeline.answer("q")

    assert result["status"] == "error"
    assert result["error_code"] == "BAD_LLM_PLAN"
    assert captured[-1]["usage"] == {
        "prompt_tokens": 7,
        "completion_tokens": 3,
        "total_tokens": 10,
    }
