"""pipeline 状态与 trace：无数据、越界、正常结果要能区分。"""
from __future__ import annotations

from src.agent import pipeline
from src.exec.duck import QueryResult
from src.plan.schema import QueryPlan, TimeRange


def _run(monkeypatch, plan: QueryPlan, qr: QueryResult | None = None):
    captured: list[dict] = []
    monkeypatch.setattr(pipeline, "_data_range", lambda: ("2016-09-04", "2018-10-17"))
    monkeypatch.setattr(pipeline, "_trace", lambda record: captured.append(record))
    if qr is not None:
        monkeypatch.setattr(pipeline.duck, "run_sql", lambda sql, params: qr)
    result = pipeline.answer("test", plan_override=plan)
    return result, captured


def test_all_null_scalar_is_no_data(monkeypatch) -> None:
    qr = QueryResult(columns=["metric_value"], rows=[(None,)], elapsed_ms=1)
    result, captured = _run(monkeypatch, QueryPlan(metrics=["gmv"]), qr)
    assert result["status"] == "no_data"
    assert result["rows"] == [] and result["row_count"] == 0
    assert captured[-1]["row_count"] == 0
    assert captured[-1]["metric_id"] == "gmv"


def test_empty_rows_is_no_data(monkeypatch) -> None:
    qr = QueryResult(columns=["metric_value"], rows=[], elapsed_ms=1)
    result, _ = _run(monkeypatch, QueryPlan(metrics=["gmv"]), qr)
    assert result["status"] == "no_data"


def test_normal_result_has_readable_columns_and_trace_fields(monkeypatch) -> None:
    qr = QueryResult(columns=["metric_value"], rows=[(1.0,)], elapsed_ms=1)
    result, captured = _run(monkeypatch, QueryPlan(metrics=["gmv"]), qr)
    assert result["status"] == "ok"
    assert result["columns"] == ["商品交易总额"]
    assert result["metric"]["unit"] == "BRL"
    record = captured[-1]
    for key in ("plan", "sql", "metric_id", "row_count", "warning"):
        assert key in record
    assert record["metric_id"] == "gmv"
    assert record["row_count"] == 1


def test_out_of_range_is_unsupported_without_sql(monkeypatch) -> None:
    plan = QueryPlan(metrics=["gmv"], time_range=TimeRange(start="2019-01-01", end="2019-12-31"))
    result, captured = _run(monkeypatch, plan)
    assert result["status"] == "unsupported"
    assert "超出数据覆盖区间" in result["message"]
    assert captured[-1]["sql"] is None


def test_unexpected_error_message_is_sanitized(monkeypatch) -> None:
    def boom(sql, params):
        raise RuntimeError("secret internal detail")

    captured: list[dict] = []
    monkeypatch.setattr(pipeline, "_data_range", lambda: ("2016-09-04", "2018-10-17"))
    monkeypatch.setattr(pipeline, "_trace", lambda record: captured.append(record))
    monkeypatch.setattr(pipeline.duck, "run_sql", boom)
    result = pipeline.answer("test", plan_override=QueryPlan(metrics=["gmv"]))
    assert result["status"] == "error"
    assert "secret internal detail" not in result["message"]
    assert captured[-1]["error_detail"]

def test_negative_metric_adds_warning_without_changing_value(monkeypatch) -> None:
    qr = QueryResult(columns=["metric_value"], rows=[(-1.0,)], elapsed_ms=1)
    result, _ = _run(monkeypatch, QueryPlan(metrics=["gmv"]), qr)
    assert result["status"] == "ok"
    assert result["rows"] == [[-1.0]]
    assert "负值" in result["warning"]

