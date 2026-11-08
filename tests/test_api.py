"""POST /query 的 HTTP 边界：状态码和响应体。"""
from __future__ import annotations

from fastapi.testclient import TestClient

from src.agent import pipeline
from src.api.main import app

client = TestClient(app)


def test_ok_returns_200(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "answer", lambda q: {
        "status": "ok", "trace_id": "t", "question": q,
        "columns": ["GMV"], "rows": [[1.0]], "row_count": 1,
        "metric": {"id": "gmv", "label": "商品交易总额", "unit": "BRL"},
    })
    r = client.post("/query", json={"question": "GMV"})
    assert r.status_code == 200
    assert r.json()["columns"] == ["GMV"]


def test_no_data_returns_200_with_message(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "answer", lambda q: {
        "status": "no_data", "trace_id": "t", "question": q,
        "columns": ["GMV"], "rows": [], "row_count": 0,
        "metric": {"id": "gmv"}, "message": "没有数据",
    })
    r = client.post("/query", json={"question": "GMV"})
    assert r.status_code == 200
    assert r.json()["status"] == "no_data"
    assert r.json()["message"] == "没有数据"


def test_clarify_and_unsupported_return_200(monkeypatch) -> None:
    for status in ("clarify", "unsupported"):
        monkeypatch.setattr(pipeline, "answer", lambda q, s=status: {
            "status": s, "trace_id": "t", "question": q, "message": "m",
        })
        r = client.post("/query", json={"question": "q"})
        assert r.status_code == 200
        assert r.json()["status"] == status


def test_bad_plan_maps_to_422(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "answer", lambda q: {
        "status": "error", "trace_id": "t", "question": q,
        "error_code": "BAD_LLM_PLAN", "message": "bad",
    })
    assert client.post("/query", json={"question": "q"}).status_code == 422


def test_timeout_maps_to_504(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "answer", lambda q: {
        "status": "error", "trace_id": "t", "question": q,
        "error_code": "QUERY_TIMEOUT", "message": "timeout",
    })
    assert client.post("/query", json={"question": "q"}).status_code == 504


def test_unknown_metric_maps_to_400(monkeypatch) -> None:
    monkeypatch.setattr(pipeline, "answer", lambda q: {
        "status": "error", "trace_id": "t", "question": q,
        "error_code": "UNKNOWN_METRIC", "message": "bad",
    })
    assert client.post("/query", json={"question": "q"}).status_code == 400


def test_blank_question_is_rejected_without_calling_pipeline(monkeypatch) -> None:
    called = {"n": 0}

    def fake(q):
        called["n"] += 1
        return {"status": "ok", "trace_id": "t", "question": q}

    monkeypatch.setattr(pipeline, "answer", fake)
    assert client.post("/query", json={"question": ""}).status_code == 422
    assert client.post("/query", json={"question": "   "}).status_code == 422
    assert called["n"] == 0
