"""指标契约的加载与二次校验。

为什么 Pydantic 校验之后还要再校验一次：
Pydantic 只保证"字段形状对"，不知道业务里有哪些指标、哪些维度可用。
语义层的价值就在这一层——它决定"能问什么"，而不是"格式对不对"。
"""
from __future__ import annotations

from datetime import date
from functools import lru_cache
from typing import Any

import yaml

from src.config import METRICS_PATH, AppError
from src.plan.schema import QueryPlan, TimeRange


@lru_cache(maxsize=1)
def load_contract() -> dict[str, Any]:
    with open(METRICS_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


def get_metrics() -> dict[str, dict]:
    return load_contract()["metrics"]


def get_dimensions() -> dict[str, dict]:
    return load_contract()["dimensions"]


def get_cte(name: str) -> str:
    ctes = load_contract()["ctes"]
    if name not in ctes:
        # 契约写错（引用了不存在的 CTE）属于配置错误，启动期就该暴露
        raise AppError("CONTRACT_ERROR", f"metrics.yaml 引用了不存在的 CTE: {name}")
    return ctes[name]


def as_of_sql() -> str:
    return load_contract()["as_of_sql"]


def describe_metrics() -> list[dict]:
    """给 MCP / LLM 用的指标目录：只暴露人能看懂的信息，不暴露 SQL。"""
    result = []
    for mid, m in get_metrics().items():
        allowed_dims = m.get("allowed_dimensions", [])
        result.append({
            "id": mid,
            "label": m["label"],
            "unit": m.get("unit"),
            "dimensions": allowed_dims,
            # filters 和 dimensions 不是一回事：TopN 指标可以按州过滤，
            # 但不能按州分组。默认沿用 allowed_dimensions，保持向后兼容。
            "filters": m.get("allowed_filters", allowed_dims),
            "default_limit": m.get("default_limit"),
            "note": m.get("note", ""),
            # 口径歧义提示：契约里写了却没人用，等于没有——catalog 必须带给 LLM
            "clarifications": m.get("clarifications", []),
        })
    return result


def _as_date(text: str | None) -> date | None:
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except (TypeError, ValueError):
        return None


def _same_month(start: str, end: str) -> bool:
    a, b = _as_date(start), _as_date(end)
    return a is not None and b is not None and (a.year, a.month) == (b.year, b.month)


def _covers_full_range(tr: TimeRange, data_range: tuple[str | None, str | None]) -> bool:
    """时间范围覆盖整个数据集时，这个过滤条件恒为真。"""
    start, end = _as_date(tr.start), _as_date(tr.end)
    data_start, data_end = _as_date(data_range[0]), _as_date(data_range[1])
    if None in (start, end, data_start, data_end):
        return False
    return start <= data_start and end >= data_end


def canonicalize(plan: QueryPlan, data_range: tuple[str | None, str | None] = (None, None)) -> QueryPlan:
    """把语义等价、形状不同的 plan 收敛成同一种写法。

    三类恒等变换，都是"模型写法差异"而不是"口径差异"：
    1) 时间范围落在一个自然月内时，"按 month 分组"不会多出任何一行，只是多一列；
    2) 指标本身没有时间字段时，覆盖全量区间的 time_range 等于没有过滤；
    3) 维度已被等值过滤钉死（state='SP' 还按 state 分组），分组键是常数，也只是多一列。

    留着它们会让同一问题返回不同形状的 SQL / 结果——评测按整行比较会误判，
    前端也要处理多种形状。只对无时间字段的指标去掉全量范围，避免稀释真实过滤。
    """
    updates: dict[str, Any] = {}

    metric = get_metrics().get(plan.metrics[0]) if plan.metrics else None
    if (
        metric
        and metric.get("time_field") in (None, "NULL")
        and plan.time_range
        and _covers_full_range(plan.time_range, data_range)
    ):
        updates["time_range"] = None

    dims = list(updates.get("dimensions", plan.dimensions))
    tr = plan.time_range
    if "month" in dims and tr and tr.start and tr.end and _same_month(tr.start, tr.end):
        dims = [d for d in dims if d != "month"]

    # 等值过滤钉死的维度：分组键只剩一个取值，按它分组不改变行数，只多一列
    pinned = {
        f.field
        for f in plan.filters
        if f.op == "=" or (f.op == "in" and isinstance(f.value, list) and len(f.value) == 1)
    }
    dropped = set(plan.dimensions) - set(dims) | (set(dims) & pinned)
    if dropped:
        dims = [d for d in dims if d not in dropped]
        updates["dimensions"] = dims
        updates["order_by"] = [o for o in plan.order_by if o.field not in dropped]

    return plan.model_copy(update=updates) if updates else plan


def time_filter_clarify(plan: QueryPlan) -> str | None:
    """无时间字段的指标被要求按时间过滤时，返回一句澄清，而不是报错。

    这是用户的合理误解（"2018 年复购率"），不是非法输入；
    直接 400 会让用户无从下手，问一句"是否看全量"更有用。
    """
    metric = get_metrics().get(plan.metrics[0]) if plan.metrics else None
    tr = plan.time_range
    if metric and metric.get("time_field") in (None, "NULL") and tr and (tr.start or tr.end):
        return (
            f"{metric['label']}没有可用于过滤的时间字段，不支持按时间范围统计。"
            f"是否改为看全量{metric['label']}（不限时间）？"
        )
    return None


def time_scope_error(plan: QueryPlan, data_range: tuple[str | None, str | None]) -> str | None:
    """时间范围完全落在数据覆盖区间之外时，返回拒答原因。

    这是 prompt 规则之外的确定性兜底：模型即使输出了 2019 年的 plan，
    也不会得到 ok + NULL 这种误导性结果。
    """
    metric = get_metrics().get(plan.metrics[0]) if plan.metrics else None
    if not metric or metric.get("time_field") in (None, "NULL"):
        return None

    tr = plan.time_range
    if not tr or not (tr.start or tr.end):
        return None

    data_start = _as_date(data_range[0])
    data_end = _as_date(data_range[1])
    if not data_start or not data_end:
        return None

    start = _as_date(tr.start) or data_start
    end = _as_date(tr.end) or data_end
    if end < data_start or start > data_end:
        return (
            f"时间范围 {tr.start or '不限'} 至 {tr.end or '不限'} 完全超出数据覆盖区间 "
            f"{data_start.isoformat()} 至 {data_end.isoformat()}，无法查询。"
        )
    return None


def time_scope_warning(plan: QueryPlan, data_range: tuple[str | None, str | None]) -> str | None:
    """时间范围只部分覆盖数据时，提醒结果已按数据边界截断。"""
    metric = get_metrics().get(plan.metrics[0]) if plan.metrics else None
    if not metric or metric.get("time_field") in (None, "NULL"):
        return None

    tr = plan.time_range
    if not tr or not (tr.start or tr.end):
        return None

    data_start = _as_date(data_range[0])
    data_end = _as_date(data_range[1])
    if not data_start or not data_end:
        return None

    start = _as_date(tr.start) or data_start
    end = _as_date(tr.end) or data_end
    if start < data_start or end > data_end:
        return (
            f"查询范围部分超出数据覆盖区间，结果已按 {data_start.isoformat()} 至 "
            f"{data_end.isoformat()} 截断。"
        )
    return None


def validate_plan(plan: QueryPlan) -> tuple[dict, list[dict]]:
    """校验 plan 是否落在契约允许的范围内，返回（指标, 用到的维度列表）。"""
    if plan.clarify or plan.unsupported_reason:
        return {}, []

    if len(plan.metrics) != 1:
        # MVP 只支持单指标：多指标若粒度不同会互相污染，宁可先不支持
        raise AppError(
            "MULTI_METRIC_UNSUPPORTED",
            "当前版本一次只能查询一个指标，请拆成多次提问",
        )

    metric_id = plan.metrics[0]
    metrics = get_metrics()
    if metric_id not in metrics:
        raise AppError(
            "UNKNOWN_METRIC",
            f"不支持的指标：{metric_id}；可用指标：{', '.join(sorted(metrics))}",
        )
    metric = metrics[metric_id]

    allowed = metric.get("allowed_dimensions", [])
    if metric.get("shape") == "topn" and plan.dimensions:
        raise AppError(
            "DIMENSION_NOT_ALLOWED",
            f"{metric['label']} 已自带分组，不能再指定维度",
        )

    used = []
    for dim_id in plan.dimensions:
        if dim_id not in get_dimensions():
            raise AppError(
                "UNKNOWN_DIMENSION",
                f"不支持的维度：{dim_id}；可用维度：{', '.join(sorted(get_dimensions()))}",
            )
        if dim_id not in allowed:
            raise AppError(
                "DIMENSION_NOT_ALLOWED",
                f"{metric['label']} 不支持按 {dim_id} 拆分，可选：{', '.join(allowed) or '无'}",
            )
        used.append(get_dimensions()[dim_id])

    # filter 和 dimensions 分开校验：TopN 指标可以按州过滤，但不能按州分组。
    allowed_filters = metric.get("allowed_filters", allowed)
    for f in plan.filters:
        if f.field not in get_dimensions():
            raise AppError(
                "UNKNOWN_DIMENSION",
                f"不支持的过滤字段：{f.field}；可用维度：{', '.join(sorted(get_dimensions()))}",
            )
        if f.field not in allowed_filters:
            raise AppError(
                "FILTER_NOT_ALLOWED",
                f"{metric['label']} 不支持按 {f.field} 过滤，可选：{', '.join(allowed_filters) or '无'}",
            )

    # 时间范围：没有时间字段的指标（如复购率）不能按时间过滤。
    # 注意 start/end 都为 null 的 time_range 等于"没有约束"，不能判成越权——
    # 模型经常为了字段完整而补一个空 time_range，那样会把合法请求打成 400。
    tr = plan.time_range
    has_time_filter = bool(tr and (tr.start or tr.end))
    if has_time_filter and metric.get("time_field") in (None, "NULL"):
        raise AppError(
            "TIME_NOT_SUPPORTED",
            f"{metric['label']} 不支持按时间范围过滤",
        )

    return metric, used
