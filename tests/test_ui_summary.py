"""UI 摘要纯函数测试：不启动 Streamlit，不连数据库。"""
from __future__ import annotations

from src.ui import summary


def _scalar_result() -> dict:
    return {
        "status": "ok",
        "question": "2018 年 1 月的 GMV 是多少",
        "columns": ["商品交易总额"],
        "rows": [[945456.2900000019]],
        "row_count": 1,
        "metric": {"id": "gmv", "label": "商品交易总额", "unit": "BRL", "additivity": "additive"},
        "plan": {"time_range": {"start": "2018-01-01", "end": "2018-01-31"}},
        "elapsed_ms": 1900,
    }


def _grouped_result() -> dict:
    return {
        "status": "ok",
        "columns": ["客户所在州", "订单数"],
        "rows": [["RN", 197], ["SP", 5000], ["RJ", 2000]],
        "row_count": 3,
        "metric": {"id": "orders", "label": "订单数", "unit": "单", "additivity": "additive"},
        "elapsed_ms": 1234,
    }


def test_summarize_scalar_uses_label_value_and_time_phrase() -> None:
    assert summary.summarize(_scalar_result()) == "2018 年 1 月的商品交易总额为 R$ 945,456.29。"


def test_summarize_grouped_picks_max_not_first_row() -> None:
    assert summary.summarize(_grouped_result()) == "共 3 个州有数据，SP 最高，为 5,000 单。"


def test_summarize_grouped_ratio_uses_percentage() -> None:
    result = {
        "status": "ok",
        "columns": ["支付方式", "支付方式占比"],
        "rows": [["credit_card", 0.739], ["boleto", 0.19]],
        "row_count": 2,
        "metric": {"id": "payment_share", "label": "支付方式占比", "unit": "ratio", "additivity": "non_additive"},
    }
    assert summary.summarize(result) == "共 2 种支付方式有数据，credit_card 最高，为 73.9%。"


def _gmv_mom_result(columns: list[str] | None = None) -> dict:
    return {
        "status": "ok",
        "columns": columns or ["月份", "GMV", "上期GMV", "环比"],
        "rows": [
            ["2017-11", 500, 100, 4.0],
            ["2017-01", 100, None, None],
            ["2018-02", 300, 200, 0.5],
        ],
        "row_count": 3,
        "metric": {"id": "gmv_mom", "label": "GMV 环比", "unit": "ratio", "additivity": "non_additive"},
    }


def test_summarize_gmv_mom_mentions_latest_gmv_and_mom() -> None:
    assert summary.summarize(_gmv_mom_result()) == (
        "2018-02 GMV 为 R$ 300.00，环比 +50.0%；"
        "区间最高环比为 2017-11（+400.0%）。"
    )


def test_gmv_mom_value_index_prefers_mom_when_columns_are_shuffled() -> None:
    result = {
        "status": "ok",
        "columns": ["月份", "环比", "GMV", "上期GMV"],
        "rows": [
            ["2018-01", None, 100, None],
            ["2018-02", 0.1, 110, 100],
            ["2018-03", -0.2, 88, 110],
        ],
        "row_count": 3,
        "metric": {"id": "gmv_mom", "label": "GMV 环比", "unit": "ratio", "additivity": "non_additive"},
    }
    assert summary._value_index(result, summary._rows(result), summary._columns(result)) == 1
    assert summary.summarize(result) == (
        "2018-03 GMV 为 R$ 88.00，环比 -20.0%；"
        "区间最高环比为 2018-02（+10.0%）。"
    )


def test_summarize_falls_back_without_numeric_columns() -> None:
    result = {"status": "ok", "columns": ["a", "b"], "rows": [["x", "y"], ["z", "w"]], "row_count": 2}
    assert summary.summarize(result) == "查询成功，共 2 行。"


def test_non_ok_results_are_safe() -> None:
    result = {"status": "clarify", "message": "需要确认口径"}
    assert summary.summarize(result) == "需要确认口径"
    assert summary.kpi_cards(result) == []
    assert summary.chart_spec(result) is None
    assert summary.badge_line(result) == ""


def test_kpi_cards_scalar() -> None:
    assert summary.kpi_cards(_scalar_result()) == [
        ("商品交易总额", "R$ 945,456.29", ""),
    ]


def test_kpi_cards_grouped_computes_top1_share() -> None:
    assert summary.kpi_cards(_grouped_result()) == [
        ("总量", "7,197 单", ""),
        ("最高", "5,000 单", "SP"),
        ("Top1 占比", "69.5%", "SP"),
    ]


def test_kpi_cards_grouped_uses_share_column_from_top_row() -> None:
    result = {
        "status": "ok",
        "columns": ["客户所在州", "订单数", "订单占比"],
        "rows": [["RN", 197, 0.1], ["SP", 5000, 0.7], ["RJ", 2000, 0.2]],
        "row_count": 3,
        "metric": {"id": "orders", "label": "订单数", "unit": "单", "additivity": "additive"},
    }
    assert summary.kpi_cards(result)[2] == ("Top1 占比", "70.0%", "SP")


def test_kpi_cards_uses_additivity_metadata_for_new_metric() -> None:
    result = {
        "status": "ok",
        "columns": ["客户所在州", "新指标"],
        "rows": [["RN", 100], ["SP", 300], ["RJ", 200]],
        "row_count": 3,
        "metric": {
            "id": "new_additive_metric",
            "label": "新指标",
            "unit": "单",
            "additivity": "additive",
        },
    }
    assert summary.kpi_cards(result)[2] == ("Top1 占比", "50.0%", "SP")


def test_kpi_cards_non_additive_without_share_shows_top_value() -> None:
    result = {
        "status": "ok",
        "columns": ["客户所在州", "客单价"],
        "rows": [["SP", 100.0], ["RJ", 80.0]],
        "row_count": 2,
        "metric": {"id": "aov", "label": "客单价", "unit": "BRL", "additivity": "non_additive"},
    }
    assert summary.kpi_cards(result) == [
        ("平均", "R$ 90.00", ""),
        ("最高", "R$ 100.00", "SP"),
        ("最低", "R$ 80.00", "RJ"),
    ]


def test_kpi_cards_gmv_mom_uses_mom_labels() -> None:
    assert summary.kpi_cards(_gmv_mom_result()) == [
        ("最新环比", "+50.0%", "2018-02"),
        ("最高环比", "+400.0%", "2017-11"),
        ("最新 GMV", "R$ 300.00", "2018-02"),
    ]


def test_kpi_cards_ratio_grouped_uses_extremes() -> None:
    result = {
        "status": "ok",
        "columns": ["客户所在州", "取消率"],
        "rows": [["SP", 0.1], ["RJ", 0.2]],
        "row_count": 2,
        "metric": {"id": "cancel_rate", "label": "取消率", "unit": "ratio", "additivity": "non_additive"},
    }
    assert summary.kpi_cards(result) == [
        ("最高", "20.0%", "RJ"),
        ("最低", "10.0%", "SP"),
        ("平均", "15.0%", ""),
    ]


def test_chart_spec_scalar_is_none() -> None:
    assert summary.chart_spec(_scalar_result()) is None


def test_chart_spec_line_for_time_series_sorted_by_time() -> None:
    result = {
        "status": "ok",
        "columns": ["月份", "GMV"],
        "rows": [["2017-02", 20], ["2017-01", 10], ["2017-03", 30]],
        "row_count": 3,
        "metric": {"id": "gmv", "label": "商品交易总额", "unit": "BRL", "additivity": "additive"},
    }
    chart = summary.chart_spec(result)
    assert chart is not None
    assert chart["kind"] == "line"
    assert chart["x"] == "月份"
    assert chart["y"] == "GMV"
    assert chart["data"]["月份"] == ["2017-01", "2017-02", "2017-03"]
    assert chart["data"]["GMV"] == [10, 20, 30]


def test_chart_spec_gmv_mom_draws_gmv_line_with_caption() -> None:
    chart = summary.chart_spec(_gmv_mom_result())
    assert chart is not None
    assert chart["kind"] == "line"
    assert chart["x"] == "月份"
    assert chart["y"] == "GMV"
    assert chart["data"]["月份"] == ["2017-01", "2017-11", "2018-02"]
    assert chart["data"]["GMV"] == [100, 500, 300]
    assert "环比" in chart["caption"]


def test_chart_spec_bar_keeps_top_10() -> None:
    rows = [[f"S{index:02d}", index] for index in range(20)]
    result = {
        "status": "ok",
        "columns": ["客户所在州", "订单数"],
        "rows": rows,
        "row_count": 20,
        "metric": {"id": "orders", "label": "订单数", "unit": "单", "additivity": "additive"},
    }
    chart = summary.chart_spec(result)
    assert chart is not None
    assert chart["kind"] == "bar"
    assert chart["x"] == "客户所在州"
    assert chart["y"] == "订单数"
    assert len(chart["data"]["客户所在州"]) == 10
    assert chart["data"]["客户所在州"][0] == "S19"
    assert chart["data"]["订单数"][0] == 19
    assert "其余 10 类" in chart["caption"]


def test_chart_spec_ratio_category_uses_horizontal_bar() -> None:
    result = {
        "status": "ok",
        "columns": ["客户所在州", "取消率"],
        "rows": [["SP", 0.1], ["RJ", 0.2]],
        "row_count": 2,
        "metric": {"id": "cancel_rate", "label": "取消率", "unit": "ratio", "additivity": "non_additive"},
    }
    chart = summary.chart_spec(result)
    assert chart is not None
    assert chart["kind"] == "bar"
    assert chart["horizontal"] is True
    assert chart["x"] == "客户所在州"
    assert chart["y"] == "取消率"
    assert chart["y_label"] == "取消率（%）"
    assert chart["data"]["客户所在州"] == ["RJ", "SP"]
    assert chart["data"]["取消率"] == [20.0, 10.0]


def test_chart_spec_payment_share_uses_horizontal_bar() -> None:
    result = {
        "status": "ok",
        "columns": ["支付方式", "支付方式占比"],
        "rows": [["boleto", 0.19], ["credit_card", 0.739]],
        "row_count": 2,
        "metric": {"id": "payment_share", "label": "支付方式占比", "unit": "ratio", "additivity": "non_additive"},
    }
    chart = summary.chart_spec(result)
    assert chart is not None
    assert chart["kind"] == "bar"
    assert chart["horizontal"] is True
    assert chart["data"]["支付方式"] == ["credit_card", "boleto"]
    assert chart["data"]["支付方式占比"] == [73.9, 19.0]


def test_chart_spec_ratio_time_series_uses_line() -> None:
    result = {
        "status": "ok",
        "columns": ["月份", "取消率"],
        "rows": [["2018-02", 0.2], ["2018-01", 0.1]],
        "row_count": 2,
        "metric": {"id": "cancel_rate", "label": "取消率", "unit": "ratio", "additivity": "non_additive"},
    }
    chart = summary.chart_spec(result)
    assert chart is not None
    assert chart["kind"] == "line"
    assert chart["y_label"] == "取消率（%）"
    assert chart["data"]["月份"] == ["2018-01", "2018-02"]
    assert chart["data"]["取消率"] == [10.0, 20.0]


def test_badge_line_format() -> None:
    assert summary.badge_line(_scalar_result()) == "✅ 口径 gmv ｜ 🔒 只读 ｜ ⏱ 1.9s ｜ 1 行"


def test_summary_handles_missing_keys() -> None:
    for result in ({}, {"status": "ok"}, {"status": "ok", "rows": None}):
        assert isinstance(summary.summarize(result), str)
        assert summary.kpi_cards(result) == []
        assert summary.chart_spec(result) is None
        assert summary.badge_line(result) == ""

