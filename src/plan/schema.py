"""QueryPlan：LLM 唯一被允许输出的结构。

为什么不用自由 SQL：SQL 是不可枚举的输入空间，无法做白名单校验；
QueryPlan 的字段是有限集合，可以逐字段校验，这是整个安全模型的前提。
"""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, Field, model_validator

AllowedOp = Literal["=", "!=", ">", ">=", "<", "<=", "in", "between"]
# 当前语义层只有 month 维度和 gmv_mom 一个环比指标。
# 保留 granularity / compare 字段是为了兼容 LLM 输出，但只允许这两个值，
# 避免 schema 里写着 day/week/quarter/year，编译器却静默忽略。
Granularity = Literal["month"]
CompareMode = Literal["none"]


def _is_scalar(value: Any) -> bool:
    """JSON 里能安全绑定到 SQL 参数的标量。"""
    return value is not None and isinstance(value, (str, int, float, bool))


def _parse_date(text: str | None) -> date | None:
    if not text or not isinstance(text, str) or len(text) < 10:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


class Filter(BaseModel):
    """一个过滤条件。field 必须是指标契约里声明过的维度 id。"""

    field: str
    op: AllowedOp
    value: Any

    @model_validator(mode="after")
    def check_value_shape(self) -> "Filter":
        # in / between 的形状必须对；空 in 在 DuckDB 里会静默返回 NULL，
        # 不能靠数据库兜底，必须在计划层拒绝。
        if self.op == "in":
            if not isinstance(self.value, list) or not self.value:
                raise ValueError("op=in 时 value 必须是非空列表")
            if not all(_is_scalar(v) for v in self.value):
                raise ValueError("op=in 时列表元素必须是标量")
        elif self.op == "between":
            if not isinstance(self.value, list) or len(self.value) != 2:
                raise ValueError("op=between 时 value 必须是两个元素的列表")
            if not all(_is_scalar(v) for v in self.value):
                raise ValueError("op=between 时列表元素必须是标量")
        elif not _is_scalar(self.value):
            raise ValueError(f"op={self.op} 时 value 必须是标量")
        return self


class OrderBy(BaseModel):
    field: str
    desc: bool = True


class TimeRange(BaseModel):
    """时间范围。start/end 用 YYYY-MM-DD，闭区间由编译器统一处理。"""

    start: str | None = None
    end: str | None = None
    granularity: Granularity = "month"
    compare: CompareMode = "none"

    @model_validator(mode="after")
    def check_dates(self) -> "TimeRange":
        start = _parse_date(self.start)
        end = _parse_date(self.end)
        if self.start and start is None:
            raise ValueError(f"start 不是合法日期：{self.start}")
        if self.end and end is None:
            raise ValueError(f"end 不是合法日期：{self.end}")
        if start and end and start > end:
            raise ValueError("start 不能晚于 end")
        return self


class QueryPlan(BaseModel):
    """一次查询的完整意图。三选一：正常查询 / 需要澄清 / 明确不支持。"""

    metrics: list[str] = Field(default_factory=list)
    dimensions: list[str] = Field(default_factory=list)
    filters: list[Filter] = Field(default_factory=list)
    time_range: TimeRange | None = None
    order_by: list[OrderBy] = Field(default_factory=list)
    # None 表示"用户没有指定数量"；编译器按指标契约的 default_limit 解析。
    limit: int | None = Field(default=None, ge=1, le=1000)

    # 澄清与不支持是"计划的一部分"，不是异常——这样 trace 能统一记录
    clarify: str | None = None
    unsupported_reason: str | None = None

    @model_validator(mode="before")
    @classmethod
    def fill_null_defaults(cls, data: Any) -> Any:
        """模型在 clarify / unsupported 时会把无关字段显式写成 null。

        null 在这里不是业务意图（"没有时间范围"就等于默认值），却会让 Pydantic
        直接抛校验错，把本该返回 unsupported 的请求打成 UNEXPECTED。
        只补默认值，不放宽任何业务校验。
        """
        if not isinstance(data, dict):
            return data
        data = dict(data)
        for key in ("metrics", "dimensions", "filters", "order_by"):
            if data.get(key) is None:
                data[key] = []
        tr = data.get("time_range")
        if isinstance(tr, dict):
            tr = dict(tr)
            if tr.get("granularity") is None:
                tr["granularity"] = "month"
            if tr.get("compare") is None:
                tr["compare"] = "none"
            data["time_range"] = tr
        return data

    @model_validator(mode="after")
    def check_exclusive(self) -> "QueryPlan":
        has_query = bool(self.metrics)
        has_special = bool(self.clarify) or bool(self.unsupported_reason)
        if self.clarify and self.unsupported_reason:
            raise ValueError("clarify 和 unsupported_reason 只能二选一")
        if has_query and has_special:
            raise ValueError("clarify / unsupported_reason 不能与 metrics 同时出现")
        if not has_query and not has_special:
            raise ValueError("至少要有 metrics，或者给出 clarify / unsupported_reason")
        if len(self.dimensions) != len(set(self.dimensions)):
            raise ValueError("dimensions 不能重复")
        return self
