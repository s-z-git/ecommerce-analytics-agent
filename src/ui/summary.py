"""页面结果摘要：只做格式化，不依赖 Streamlit，也不调用 LLM。

为什么单独放纯函数：同一个结果要同时生成结论、KPI、图表和徽章；
把这些判断从渲染层拆出来后，可以直接用字典单测，不需要启动页面。
"""
from __future__ import annotations

import calendar
import re
from datetime import date
from typing import Any

_TIME_LABELS = {"month", "月份", "月", "日期", "date", "时间"}
_RATIO_HINTS = ("占比", "率", "环比", "share", "ratio", "mom")
_NON_ADDITIVE_IDS = {
    "aov",
    "cancel_rate",
    "delay_rate",
    "delivery_days",
    "repeat_rate",
    "avg_rating",
    "payment_share",
    "gmv_mom",
}
_ADDITIVE_IDS = {"gmv", "orders", "category_revenue", "seller_revenue"}


def _rows(result: dict[str, Any]) -> list[list[Any]]:
    rows = result.get("rows")
    if not isinstance(rows, list):
        return []
    return [list(row) for row in rows if isinstance(row, (list, tuple))]


def _columns(result: dict[str, Any]) -> list[str]:
    columns = result.get("columns")
    if not isinstance(columns, list):
        return []
    return [str(column) for column in columns]


def _metric(result: dict[str, Any]) -> dict[str, Any]:
    metric = result.get("metric")
    return metric if isinstance(metric, dict) else {}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric_indices(rows: list[list[Any]], columns: list[str]) -> list[int]:
    indices: list[int] = []
    for index in range(len(columns)):
        values = [row[index] for row in rows if index < len(row) and row[index] is not None]
        if values and all(_is_number(value) for value in values):
            indices.append(index)
    return indices


def _is_ratio_label(label: str) -> bool:
    lower = label.lower()
    return any(hint in lower for hint in _RATIO_HINTS)


def _unit_for_column(column: str, metric_unit: str | None) -> str | None:
    """同一个结果里可能混有金额和比率列；格式化以列名为准。"""
    if column.strip().upper() == "GMV":
        return "BRL"
    if _is_ratio_label(column):
        return "ratio"
    return metric_unit


def _time_index(columns: list[str], rows: list[list[Any]]) -> int | None:
    for index, label in enumerate(columns):
        if label.strip().lower() in _TIME_LABELS:
            return index
    # 列名不可靠时，再看值是否像 2018-01 / 2018-01-01。
    for index in range(len(columns)):
        values = [str(row[index]) for row in rows[:5] if index < len(row) and row[index] is not None]
        if values and all(re.fullmatch(r"\d{4}-\d{2}(-\d{2})?", value) for value in values):
            return index
    return None


def _value_index(
    result: dict[str, Any], rows: list[list[Any]], columns: list[str]
) -> int | None:
    numeric = _numeric_indices(rows, columns)
    if not numeric:
        return None

    metric = _metric(result)
    metric_id = str(metric.get("id") or "")
    label = str(metric.get("label") or "")

    # gmv_mom 同时返回 GMV 和环比；用户问的是环比，优先选环比列。
    if metric_id == "gmv_mom":
        for index in numeric:
            column = columns[index].strip().lower()
            if "环比" in column or column in {"mom", "month_over_month"}:
                return index
        for index in numeric:
            if columns[index].strip().upper() == "GMV":
                return index

    for index in numeric:
        column = columns[index]
        if label and (column == label or label in column or column in label):
            return index

    non_ratio = [index for index in numeric if not _is_ratio_label(columns[index])]
    if non_ratio:
        return non_ratio[0]
    return numeric[0]


def _share_index(
    result: dict[str, Any], rows: list[list[Any]], columns: list[str], value_index: int
) -> int | None:
    for index in _numeric_indices(rows, columns):
        if index != value_index and "占比" in columns[index]:
            return index
    return None


def _fmt_value(value: Any, unit: str | None = None) -> str:
    if value is None:
        return "—"
    if not _is_number(value):
        return str(value)

    if unit == "ratio":
        return f"{value * 100:.1f}%"
    if unit == "BRL":
        if value < 0:
            return f"-R$ {abs(value):,.2f}"
        return f"R$ {value:,.2f}"
    if unit == "单":
        return f"{value:,.0f} 单"
    if unit == "天":
        return f"{value:,.1f} 天"
    if unit == "分":
        return f"{value:,.2f} 分"
    if isinstance(value, float) and not value.is_integer():
        return f"{value:,.2f}"
    return f"{value:,.0f}"


def _fmt_signed_ratio(value: Any) -> str:
    if not _is_number(value):
        return "—"
    return f"{value * 100:+.1f}%"


def _gmv_mom_columns(
    rows: list[list[Any]], columns: list[str]
) -> tuple[int | None, int | None, int | None]:
    month_index = _time_index(columns, rows)
    gmv_index = next(
        (index for index, column in enumerate(columns) if column.strip().upper() == "GMV"),
        None,
    )
    mom_index = next(
        (
            index for index, column in enumerate(columns)
            if "环比" in column or column.strip().lower() in {"mom", "month_over_month"}
        ),
        None,
    )
    return month_index, gmv_index, mom_index


def _gmv_mom_summary(rows: list[list[Any]], columns: list[str]) -> str | None:
    """环比指标同时展示最新 GMV 和最高环比，避免只答一个孤立数字。"""
    month_index, gmv_index, mom_index = _gmv_mom_columns(rows, columns)
    if month_index is None or gmv_index is None:
        return None

    ordered = sorted(rows, key=lambda row: str(row[month_index]))
    gmv_rows = [row for row in ordered if _is_number(row[gmv_index])]
    if not gmv_rows:
        return None

    latest = gmv_rows[-1]
    latest_month = str(latest[month_index])
    latest_gmv = _fmt_value(latest[gmv_index], "BRL")
    if mom_index is not None and _is_number(latest[mom_index]):
        latest_part = f"{latest_month} GMV 为 {latest_gmv}，环比 {_fmt_signed_ratio(latest[mom_index])}"
    else:
        latest_part = f"{latest_month} GMV 为 {latest_gmv}，暂无可比环比"

    mom_rows = [
        row for row in ordered
        if mom_index is not None and _is_number(row[mom_index])
    ]
    if not mom_rows:
        return f"{latest_part}。"

    top = max(mom_rows, key=lambda row: row[mom_index])
    top_part = f"区间最高环比为 {top[month_index]}（{_fmt_signed_ratio(top[mom_index])}）"
    return f"{latest_part}；{top_part}。"


def _gmv_mom_cards(rows: list[list[Any]], columns: list[str]) -> list[tuple[str, str, str]]:
    month_index, gmv_index, mom_index = _gmv_mom_columns(rows, columns)
    if month_index is None or gmv_index is None:
        return []

    ordered = sorted(rows, key=lambda row: str(row[month_index]))
    gmv_rows = [row for row in ordered if _is_number(row[gmv_index])]
    if not gmv_rows:
        return []

    latest = gmv_rows[-1]
    latest_month = str(latest[month_index])
    latest_mom = "—"
    if mom_index is not None and _is_number(latest[mom_index]):
        latest_mom = _fmt_signed_ratio(latest[mom_index])

    mom_rows = [
        row for row in ordered
        if mom_index is not None and _is_number(row[mom_index])
    ]
    if mom_rows:
        top = max(mom_rows, key=lambda row: row[mom_index])
        top_mom = ("最高环比", _fmt_signed_ratio(top[mom_index]), str(top[month_index]))
    else:
        top_mom = ("最高环比", "—", "")

    return [
        ("最新环比", latest_mom, latest_month),
        top_mom,
        ("最新 GMV", _fmt_value(latest[gmv_index], "BRL"), latest_month),
    ]


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or len(value) < 10:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _time_phrase(plan: Any) -> str:
    if not isinstance(plan, dict):
        return ""
    time_range = plan.get("time_range")
    if not isinstance(time_range, dict):
        return ""
    start = _parse_date(time_range.get("start"))
    end = _parse_date(time_range.get("end"))
    if start is None or end is None:
        return ""

    if start.year == end.year and start.month == end.month:
        last_day = calendar.monthrange(start.year, start.month)[1]
        if start.day == 1 and end.day == last_day:
            return f"{start.year} 年 {start.month} 月"
    if start.year == end.year and (start.month, start.day) == (1, 1) and (end.month, end.day) == (6, 30):
        return f"{start.year} 年上半年"
    if start.year == end.year and (start.month, start.day) == (1, 1) and (end.month, end.day) == (12, 31):
        return f"{start.year} 年"
    return f"{start.isoformat()} 至 {end.isoformat()}"


def _dimension_unit(label: str) -> str:
    if "州" in label:
        return "个州"
    if "品类" in label:
        return "个品类"
    if "卖家" in label:
        return "个卖家"
    if "支付方式" in label:
        return "种支付方式"
    return f"个{label}"


def _series_summary(
    result: dict[str, Any],
    rows: list[list[Any]],
    columns: list[str],
    time_index: int,
    value_index: int,
) -> str:
    metric = _metric(result)
    label = columns[value_index] or str(metric.get("label") or "结果")
    unit = _unit_for_column(label, metric.get("unit"))
    ordered = sorted(rows, key=lambda row: str(row[time_index]))
    valid = [row for row in ordered if _is_number(row[value_index])]
    if not valid:
        return f"查询成功，共 {len(rows)} 行。"

    first = str(valid[0][time_index])
    last = str(valid[-1][time_index])
    top = max(valid, key=lambda row: row[value_index])
    top_label = str(top[time_index])
    top_value = _fmt_value(top[value_index], unit)
    if first == last:
        return f"{first} 的{label}为 {top_value}。"
    return f"{first} 至 {last} 共 {len(valid)} 个月，最高为 {top_label}（{top_value}）。"


def _grouped_summary(
    result: dict[str, Any],
    rows: list[list[Any]],
    columns: list[str],
    value_index: int,
) -> str:
    metric = _metric(result)
    unit = _unit_for_column(columns[value_index], metric.get("unit"))
    time_index = _time_index(columns, rows)
    category_index = next(
        (index for index in range(len(columns)) if index not in (value_index, time_index)),
        None,
    )
    if category_index is None:
        return f"查询成功，共 {len(rows)} 行。"

    category_label = columns[category_index]
    valid = [row for row in rows if _is_number(row[value_index])]
    if not valid:
        return f"查询成功，共 {len(rows)} 行。"

    top = max(valid, key=lambda row: row[value_index])
    top_name = str(top[category_index])
    top_value = _fmt_value(top[value_index], unit)
    count_text = f"共 {len(valid)} {_dimension_unit(category_label)}有数据"

    if unit == "ratio":
        return f"{count_text}，{top_name} 最高，为 {top_value}。"

    share_index = _share_index(result, rows, columns, value_index)
    if share_index is not None:
        share = _fmt_value(top[share_index] if share_index < len(top) else None, "ratio")
        return f"{count_text}，{top_name} 最高，为 {top_value}（占 {share}）。"
    return f"{count_text}，{top_name} 最高，为 {top_value}。"


def summarize(result: dict[str, Any]) -> str:
    """按结果形态生成一句话结论；识别不了就老实说行数。"""
    if result.get("status") != "ok":
        return str(result.get("message") or "本次查询未完成。")

    rows = _rows(result)
    columns = _columns(result)
    metric = _metric(result)
    unit = metric.get("unit")
    if not rows or not columns:
        return f"查询成功，共 {result.get('row_count', 0)} 行。"

    if str(metric.get("id") or "") == "gmv_mom":
        gmv_mom_text = _gmv_mom_summary(rows, columns)
        if gmv_mom_text:
            return gmv_mom_text

    value_index = _value_index(result, rows, columns)
    if value_index is None:
        return f"查询成功，共 {len(rows)} 行。"

    if len(rows) == 1 and len(columns) == 1:
        label = columns[value_index] or str(metric.get("label") or "结果")
        value = _fmt_value(rows[0][value_index], unit)
        prefix = _time_phrase(result.get("plan"))
        if prefix:
            return f"{prefix}的{label}为 {value}。"
        return f"{label}为 {value}。"

    time_index = _time_index(columns, rows)
    if time_index is not None and time_index != value_index:
        return _series_summary(result, rows, columns, time_index, value_index)

    return _grouped_summary(result, rows, columns, value_index)


def kpi_cards(result: dict[str, Any]) -> list[tuple[str, str, str]]:
    """最多 3 个业务 KPI；不再用“行数”充当指标。"""
    if result.get("status") != "ok":
        return []

    rows = _rows(result)
    columns = _columns(result)
    metric = _metric(result)
    unit = metric.get("unit")
    metric_id = str(metric.get("id") or "未知")
    value_index = _value_index(result, rows, columns)
    if not rows or value_index is None:
        return []

    if metric_id == "gmv_mom":
        cards = _gmv_mom_cards(rows, columns)
        if cards:
            return cards

    if len(rows) == 1 and len(columns) == 1:
        label = columns[value_index] or str(metric.get("label") or "结果")
        return [(label, _fmt_value(rows[0][value_index], unit), "")]

    time_index = _time_index(columns, rows)
    if time_index is not None and time_index != value_index:
        valid = [row for row in rows if _is_number(row[value_index])]
        if not valid:
            return []
        ordered = sorted(valid, key=lambda row: str(row[time_index]))
        first, latest = ordered[0], ordered[-1]
        top = max(ordered, key=lambda row: row[value_index])
        value_unit = _unit_for_column(columns[value_index], unit)
        if _is_number(first[value_index]) and first[value_index] != 0:
            delta = (latest[value_index] - first[value_index]) / first[value_index]
            change = ("区间变化", _fmt_signed_ratio(delta),
                      f"{first[time_index]} → {latest[time_index]}")
        else:
            avg = sum(row[value_index] for row in ordered) / len(ordered)
            change = ("区间平均", _fmt_value(avg, value_unit), "")
        return [
            ("最新", _fmt_value(latest[value_index], value_unit), str(latest[time_index])),
            ("最高", _fmt_value(top[value_index], value_unit), str(top[time_index])),
            change,
        ]

    valid = [row for row in rows if _is_number(row[value_index])]
    if not valid:
        return []
    values = [row[value_index] for row in valid]

    if len(valid) == 1:
        row = valid[0]
        category_index = next((i for i in range(len(columns)) if i != value_index), 0)
        return [(
            str(metric.get("label") or columns[value_index]),
            _fmt_value(row[value_index], unit),
            str(row[category_index]),
        )]

    if unit == "ratio":
        top = max(valid, key=lambda row: row[value_index])
        low = min(valid, key=lambda row: row[value_index])
        category_index = next((i for i in range(len(columns)) if i != value_index), 0)
        avg = sum(values) / len(values)
        return [
            ("最高", _fmt_value(top[value_index], unit), str(top[category_index])),
            ("最低", _fmt_value(low[value_index], unit), str(low[category_index])),
            ("平均", _fmt_value(avg, unit), ""),
        ]

    additivity = metric.get("additivity")
    is_non_additive = additivity == "non_additive" or (
        additivity is None and metric_id in _NON_ADDITIVE_IDS
    )
    top = max(valid, key=lambda row: row[value_index])
    low = min(valid, key=lambda row: row[value_index])
    category_index = next((i for i in range(len(columns)) if i != value_index), 0)
    top_name = str(top[category_index])
    low_name = str(low[category_index])

    if is_non_additive:
        avg = sum(values) / len(values)
        return [
            ("平均", _fmt_value(avg, unit), ""),
            ("最高", _fmt_value(top[value_index], unit), top_name),
            ("最低", _fmt_value(low[value_index], unit), low_name),
        ]

    total = sum(values)
    share_index = _share_index(result, rows, columns, value_index)
    if share_index is not None:
        share = ("Top1 占比", _fmt_value(top[share_index], "ratio"), top_name)
    elif total > 0:
        share = ("Top1 占比", _fmt_value(top[value_index] / total, "ratio"), top_name)
    else:
        share = ("最高", _fmt_value(top[value_index], unit), top_name)
    return [
        ("总量", _fmt_value(total, unit), ""),
        ("最高", _fmt_value(top[value_index], unit), top_name),
        share,
    ]


def chart_spec(result: dict[str, Any]) -> dict[str, Any] | None:
    """只画有把握的图：时间序列用折线，类别比较用柱状，其余不画。"""
    if result.get("status") != "ok":
        return None

    rows = _rows(result)
    columns = _columns(result)
    if str(_metric(result).get("id") or "") == "gmv_mom":
        month_index, gmv_index, _mom_index = _gmv_mom_columns(rows, columns)
        if month_index is None or gmv_index is None:
            return None
        valid = [
            row for row in rows
            if row[month_index] is not None and _is_number(row[gmv_index])
        ]
        if len(valid) < 2:
            return None
        valid.sort(key=lambda row: str(row[month_index]))
        return {
            "kind": "line",
            "x": columns[month_index],
            "y": columns[gmv_index],
            "data": {
                columns[month_index]: [str(row[month_index]) for row in valid],
                columns[gmv_index]: [row[gmv_index] for row in valid],
            },
            "unit": "BRL",
            "caption": "折线为 GMV；环比见上方 KPI",
        }

    value_index = _value_index(result, rows, columns)
    if len(rows) < 2 or value_index is None:
        return None

    metric_unit = _metric(result).get("unit")
    unit = _unit_for_column(columns[value_index], metric_unit)
    is_ratio = _is_ratio_label(columns[value_index]) or unit == "ratio"
    y_label = f"{columns[value_index]}（%）" if is_ratio else columns[value_index]

    def chart_value(value: float, ratio: bool = is_ratio) -> float:
        return value * 100 if ratio else value

    time_index = _time_index(columns, rows)
    if time_index is not None and time_index != value_index:
        valid = [
            row for row in rows
            if row[time_index] is not None and _is_number(row[value_index])
        ]
        if len(valid) < 2:
            return None
        valid.sort(key=lambda row: str(row[time_index]))
        return {
            "kind": "line",
            "x": columns[time_index],
            "y": columns[value_index],
            "y_label": y_label,
            "data": {
                columns[time_index]: [str(row[time_index]) for row in valid],
                columns[value_index]: [chart_value(row[value_index]) for row in valid],
            },
            "unit": unit,
        }

    category_index = next(
        (
            index for index in range(len(columns))
            if index != value_index
            and not _is_ratio_label(columns[index])
            and not (index < len(rows[0]) and _is_number(rows[0][index]))
        ),
        None,
    )
    if category_index is None:
        return None

    valid = [
        row for row in rows
        if row[category_index] is not None and _is_number(row[value_index])
    ]
    if len(valid) < 2:
        return None
    valid.sort(key=lambda row: row[value_index], reverse=True)
    total_categories = len(valid)
    valid = valid[:10]
    hidden = total_categories - len(valid)
    return {
        "kind": "bar",
        "horizontal": is_ratio,
        "x": columns[category_index],
        "y": columns[value_index],
        "y_label": y_label,
        "data": {
            columns[category_index]: [str(row[category_index]) for row in valid],
            columns[value_index]: [chart_value(row[value_index]) for row in valid],
        },
        "unit": unit,
        "caption": ("仅显示前 10 类，其余 %d 类已计入总量。" % hidden) if hidden else "",
    }


def badge_line(result: dict[str, Any]) -> str:
    """结果徽章：口径、只读、耗时、行数。非 ok 或无结果时不生成徽章。"""
    if result.get("status") != "ok" or not _rows(result):
        return ""

    metric_id = str(_metric(result).get("id") or "未知")
    elapsed_ms = result.get("elapsed_ms")
    if _is_number(elapsed_ms):
        elapsed = f"{elapsed_ms / 1000:.1f}s"
    else:
        elapsed = "—"

    row_count = result.get("row_count")
    if not _is_number(row_count):
        row_count = len(_rows(result))
    return f"✅ 口径 {metric_id} ｜ 🔒 只读 ｜ ⏱ {elapsed} ｜ {row_count} 行"
