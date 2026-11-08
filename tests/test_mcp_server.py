"""MCP 工具函数：对外只暴露指标目录与自然语言问答。"""
from __future__ import annotations

import pytest

from src import mcp_server


def test_list_metrics_exposes_contract_without_sql() -> None:
    metrics = mcp_server.list_metrics()
    assert len(metrics) == 12
    item = metrics[0]
    assert {"id", "label", "unit", "dimensions", "note"} <= set(item)
    # SQL 模板属于内部实现，不能通过外部入口泄露
    assert "sql" not in item


def test_query_delegates_to_pipeline(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = {}

    def fake_answer(question: str) -> dict:
        seen["question"] = question
        return {"status": "ok", "rows": [[1]], "columns": ["x"]}

    monkeypatch.setattr(mcp_server.pipeline, "answer", fake_answer)
    result = mcp_server.query("2018 年 GMV")
    assert result["status"] == "ok"
    assert seen["question"] == "2018 年 GMV"
