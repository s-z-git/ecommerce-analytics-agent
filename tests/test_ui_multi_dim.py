"""B5 双维多序列图测试：纯函数，不连数据库。"""
from __future__ import annotations

from src.ui import summary


def _result(columns, rows, metric_id="gmv", unit="BRL"):
    return {
        "status": "ok",
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "metric": {"id": metric_id, "label": "商品交易总额", "unit": unit, "additivity": "additive"},
    }


def _two_dim(states=("SP", "RJ", "MG"), reverse=False):
    months = ["2018-01", "2018-02", "2018-03"]
    rows = [[m, s, 100.0 + i] for i, (m, s) in enumerate((m, s) for m in months for s in states)]
    if reverse:
        rows = [[s, m, v] for m, s, v in rows]
        return _result(["客户所在州", "月份", "商品交易总额"], rows)
    return _result(["月份", "客户所在州", "商品交易总额"], rows)


def test_two_dim_multi_line_x_unique_is_months():
    spec = summary.chart_spec(_two_dim())
    assert spec["kind"] == "multi_line"
    assert spec["data"]["x_values"] == ["2018-01", "2018-02", "2018-03"]
    assert len(spec["data"]["series"]) == 3


def test_state_month_order_still_uses_month_as_x():
    spec = summary.chart_spec(_two_dim(reverse=True))
    assert spec["kind"] == "multi_line"
    assert spec["x"] == "月份"


def test_top8_and_caption():
    states = tuple("S%d" % i for i in range(12))
    spec = summary.chart_spec(_two_dim(states=states))
    assert len(spec["data"]["series"]) == 8
    assert "8" in spec["caption"] and "12" in spec["caption"]


def test_three_dims_not_multi_line():
    result = _result(
        ["月份", "客户所在州", "支付方式", "商品交易总额"],
        [["2018-01", "SP", "credit_card", 100.0], ["2018-02", "RJ", "boleto", 200.0]],
    )
    spec = summary.chart_spec(result)
    assert spec is None or spec["kind"] != "multi_line"


def test_one_dim_still_line():
    result = _result(["月份", "商品交易总额"], [["2018-01", 100.0], ["2018-02", 200.0]])
    spec = summary.chart_spec(result)
    assert spec["kind"] == "line"
    assert len(spec["data"]["月份"]) == 2
