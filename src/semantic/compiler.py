"""把 QueryPlan 渲染成只读 SQL。

这是全项目唯一的 SQL 出口：除了这里，任何地方都不允许拼 SQL。
参数化 + 白名单 + 结构化输入，三者合起来才构成"模型不能越权"。
"""
from __future__ import annotations

import calendar
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from src.config import AppError
from src.plan.schema import Filter, QueryPlan, TimeRange
from src.semantic import registry


@dataclass
class CompiledQuery:
    sql: str
    params: list[Any] = field(default_factory=list)
    metric_id: str = ""
    metric_label: str = ""
    unit: str | None = None
    note: str = ""
    dimensions: list[str] = field(default_factory=list)
    # pipeline 用它判断"标量结果是不是全 NULL"
    metric_column: str = "metric_value"
    # 原始列名 -> 人类可读列名；UI/API 不再直接暴露 dim_0 / metric_value
    column_labels: dict[str, str] = field(default_factory=dict)


def _render_filter(f: Filter, expr: str) -> tuple[str, list[Any]]:
    """单个过滤条件 → SQL 片段 + 参数。值一律走占位符，绝不拼进字符串。"""
    if f.op == "in":
        if not isinstance(f.value, list) or not f.value:
            raise AppError("INVALID_FILTER", "op=in 的 value 必须是非空列表")
        placeholders = ", ".join(["?"] * len(f.value))
        return f"{expr} IN ({placeholders})", list(f.value)
    if f.op == "between":
        if not isinstance(f.value, list) or len(f.value) != 2:
            raise AppError("INVALID_FILTER", "op=between 的 value 必须是两个元素的列表")
        return f"{expr} BETWEEN ? AND ?", [f.value[0], f.value[1]]
    return f"{expr} {f.op} ?", [f.value]


def _render_time(tf: str, tr: TimeRange | None) -> tuple[str, list[Any]]:
    """时间条件。end 按"含当天"处理：< 次日零点。"""
    if tr is None:
        return "", []
    parts: list[str] = []
    params: list[Any] = []
    if tr.start:
        parts.append(f"{tf} >= CAST(? AS TIMESTAMP)")
        params.append(tr.start)
    if tr.end:
        parts.append(f"{tf} < CAST(? AS DATE) + INTERVAL 1 DAY")
        params.append(tr.end)
    if not parts:
        return "", []
    return " AND ".join(parts), params


def _collect_ctes(metric: dict, dimensions: list[dict]) -> list[str]:
    """按出现顺序收集需要的前置 CTE。顺序很重要：cust_orders 依赖 orders/customers。"""
    order = ["items_agg", "pay_agg", "review_agg", "pay_types", "cust_orders"]
    needed: set[str] = set(metric.get("ctes") or [])
    for d in dimensions:
        needed.update(d.get("ctes") or [])
    return [name for name in order if name in needed]


def _render_dims(plan: QueryPlan, metric: dict) -> tuple[list[str], list[str], list[str]]:
    """返回（选择列, 分组列, 需要的 join 片段）。"""
    selects: list[str] = []
    joins: list[str] = []

    if metric.get("shape") == "topn":
        fixed = metric["fixed_dimension"]
        selects.append(f"{fixed['expr']} AS dim_0")
        return selects, ["dim_0"], joins

    for i, dim_id in enumerate(plan.dimensions):
        dim = registry.get_dimensions()[dim_id]
        selects.append(f"{dim['expr']} AS dim_{i}")
        for j in dim.get("joins") or []:
            if j not in joins:
                joins.append(j)

    return selects, [f"dim_{i}" for i in range(len(plan.dimensions))], joins


def _resolve_limit(plan: QueryPlan, metric: dict) -> int:
    """用户没指定 limit 时，使用指标契约的 default_limit。"""
    if plan.limit is not None:
        return plan.limit
    return int(metric.get("default_limit", 100))


def _align_series_range(tr: TimeRange | None) -> TimeRange | None:
    """环比只比较完整自然月：把用户的部分月份裁掉。

    例：start=2017-01-15 -> 从 2017-02-01 开始；
        end=2017-09-01   -> 到 2017-08-31 结束。
    否则 9 月只取到 1 号，却会拿它和 8 月整月算环比。
    """
    if tr is None:
        return None

    start = date.fromisoformat(tr.start[:10]) if tr.start else None
    end = date.fromisoformat(tr.end[:10]) if tr.end else None

    if start and start.day != 1:
        start = date(start.year + 1, 1, 1) if start.month == 12 else date(start.year, start.month + 1, 1)

    if end:
        last_day = date(end.year, end.month, calendar.monthrange(end.year, end.month)[1])
        if end != last_day:
            if end.month == 1:
                end = date(end.year - 1, 12, 31)
            else:
                prev_last = calendar.monthrange(end.year, end.month - 1)[1]
                end = date(end.year, end.month - 1, prev_last)

    return TimeRange(
        start=start.isoformat() if start else None,
        end=end.isoformat() if end else None,
    )


def compile_plan(plan: QueryPlan) -> CompiledQuery:
    if len(plan.metrics) != 1:
        raise AppError("MULTI_METRIC_UNSUPPORTED", "当前版本一次只能查询一个指标")
    metric_id = plan.metrics[0]
    metric = registry.get_metrics().get(metric_id)
    if metric is None:
        raise AppError("UNKNOWN_METRIC", f"不支持的指标：{metric_id}")

    dimensions = []
    for dim_id in plan.dimensions:
        dim = registry.get_dimensions().get(dim_id)
        if dim is None:
            raise AppError("UNKNOWN_DIMENSION", f"不支持的维度：{dim_id}")
        dimensions.append(dim)

    time_sql, time_params = _render_time(metric.get("time_field", "NULL"), plan.time_range)

    # 过滤条件用的是维度 id，要换成该维度的真实表达式。
    # validate_plan 已经做过一次；compiler 再查一次，保证单独调用也不会越权。
    allowed_filters = metric.get("allowed_filters", metric.get("allowed_dimensions", []))
    for f in plan.filters:
        if f.field not in allowed_filters:
            raise AppError("FILTER_NOT_ALLOWED", f"{metric['label']} 不支持按 {f.field} 过滤")

    filter_sql: list[str] = []
    filter_params: list[Any] = []
    filter_joins: list[str] = []
    for f in plan.filters:
        dim = registry.get_dimensions().get(f.field)
        if dim is None:
            # validate_plan 已经拦过一次，这里再拦是因为 compile 可能被单独调用
            raise AppError("UNKNOWN_DIMENSION", f"不支持的过滤字段：{f.field}")
        sql_part, params = _render_filter(f, dim["expr"])
        filter_sql.append(sql_part)
        filter_params.extend(params)
        # 过滤字段的表达式可能引用别的表（如 c.customer_state），
        # 不把它的 JOIN 带上就会拼出引用不存在表的 SQL（Binder Error）
        for j in dim.get("joins") or []:
            if j not in filter_joins:
                filter_joins.append(j)

    limit = _resolve_limit(plan, metric)

    # 时间序列型指标自带完整 SQL，只注入时间条件，不参与通用拼装
    if metric.get("shape") == "series":
        if plan.dimensions:
            raise AppError("DIMENSION_NOT_ALLOWED", f"{metric['label']} 不支持分组维度")
        if plan.filters:
            raise AppError("FILTER_NOT_ALLOWED", f"{metric['label']} 不支持过滤条件")
        complete_start = metric.get("complete_month_start")
        complete_end = metric.get("complete_month_end")
        if not complete_start or not complete_end:
            raise AppError("CONTRACT_ERROR", f"{metric_id} 缺少 complete_month_start/end")
        series_tr = _align_series_range(plan.time_range)
        series_time_sql, series_time_params = _render_time(metric.get("time_field", "NULL"), series_tr)
        sql = metric["sql"]
        for placeholder, value in (
            ("{time_filter}", series_time_sql or "1 = 1"),
            ("{complete_month_start}", str(complete_start)),
            ("{complete_month_end}", str(complete_end)),
        ):
            sql = sql.replace(placeholder, value)
        return CompiledQuery(
            sql=sql.strip(),
            params=series_time_params + [limit],
            metric_id=metric_id,
            metric_label=metric["label"],
            unit=metric.get("unit"),
            note=metric.get("note", ""),
            metric_column="gmv",
            column_labels={"month": "月份", "gmv": "GMV", "prev_v": "上期GMV", "mom": "环比"},
        )

    where_parts = [f"({metric['base_filter']})"]
    where_parts.extend(f"({s})" for s in filter_sql)
    if time_sql:
        where_parts.append(f"({time_sql})")
    where_sql = " AND ".join(where_parts)

    cte_names = _collect_ctes(metric, dimensions)
    cte_sql = ",\n".join(f"{name} AS (\n{registry.get_cte(name).strip()}\n)" for name in cte_names)

    select_dims, group_cols, dim_joins = _render_dims(plan, metric)
    for j in filter_joins:
        if j not in dim_joins:
            dim_joins.append(j)

    select_parts = list(select_dims)
    select_parts.append(f"{metric['expr']} AS metric_value")

    order_parts: list[str] = []
    for ob in plan.order_by:
        # 允许按指标本身或已选维度排序，其余一律忽略（防止拼出未知列）
        if ob.field in (metric_id, "metric_value"):
            order_parts.append("metric_value " + ("DESC" if ob.desc else "ASC"))
        elif ob.field in plan.dimensions:
            idx = plan.dimensions.index(ob.field)
            order_parts.append(f"dim_{idx} " + ("DESC" if ob.desc else "ASC"))

    # TopN 必须有确定性顺序：金额并列时按维度值打破平局，
    # 否则 LIMIT 10 的边界行会随执行计划变化。
    if metric.get("shape") == "topn":
        if not order_parts:
            order_parts.append("metric_value DESC")
        if not any(part.startswith("dim_0 ") for part in order_parts):
            order_parts.append("dim_0 ASC")

    sql_lines = []
    if cte_sql:
        sql_lines.append("WITH " + cte_sql)
    sql_lines.append("SELECT " + ", ".join(select_parts))
    sql_lines.append("FROM " + metric["from"])
    for j in dim_joins:
        sql_lines.append(j)
    sql_lines.append("WHERE " + where_sql)
    if group_cols:
        sql_lines.append("GROUP BY " + ", ".join(group_cols))
    if order_parts:
        sql_lines.append("ORDER BY " + ", ".join(order_parts))
    sql_lines.append("LIMIT ?")

    params = filter_params + time_params + [limit]

    if metric.get("shape") == "topn":
        column_labels = {
            "dim_0": metric["fixed_dimension"]["label"],
            "metric_value": metric["label"],
        }
    else:
        column_labels = {
            f"dim_{i}": dim["label"] for i, dim in enumerate(dimensions)
        }
        column_labels["metric_value"] = metric["label"]

    return CompiledQuery(
        sql="\n".join(sql_lines),
        params=params,
        metric_id=metric_id,
        metric_label=metric["label"],
        unit=metric.get("unit"),
        note=metric.get("note", ""),
        dimensions=list(plan.dimensions),
        metric_column="metric_value",
        column_labels=column_labels,
    )
