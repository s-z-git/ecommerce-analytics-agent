"""LLM 只做一件事：把中文问题翻译成 QueryPlan。

约束全部写在 prompt 里 + 输出用 JSON + 解析后强制走 Pydantic，
三道一起用，才能保证模型"想乱来也写不出 SQL"。
"""
from __future__ import annotations

import json
from typing import Any

from openai import OpenAI
from pydantic import ValidationError

from src.config import AppError, DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, LLM_MODEL
from src.plan.schema import QueryPlan
from src.semantic import registry

SYSTEM_PROMPT = """你是一个电商数据分析助手，只负责把用户问题翻译成结构化查询计划 JSON。

规则：
1. 你绝不能输出 SQL，只能输出下面定义的 JSON 字段。
2. 一次只能选一个指标（metrics 数组里只能有一个元素）。
3. dimensions 的 field 必须在指标的"允许维度"里；filters 的 field 必须在"允许过滤"里，两者不是同一个集合。filters 目前只用于 state，且值必须使用维度示例里的代码（例如 SP，不要写"圣保罗州"）。时间范围统一放 time_range，不要用 month 过滤。
4. 用户说法有歧义时输出 clarify 提出一个具体问题，不要猜，也不要自行选一个口径。典型：只说"销售额"/"营业额"（没指明是 GMV、支付金额还是别的）时必须 clarify；但"卖家收入""品类收入"这类明确指向某个指标的表述是清楚的，正常查询。
5. 用户要求删除/修改数据、查询系统信息、或者问了数据里没有的东西时，输出 unsupported_reason。
6. 时间范围必须落在数据覆盖区间内（见 data_range）。用户说"最近一个月"时，按数据截止日期往前推，不要按今天算。
7. clarify / unsupported_reason 有值时，metrics/dimensions/filters 输出空数组，time_range 和 limit 输出 null；不要用默认值占位。
8. 用户说法命中某指标的口径提醒（clarifications，如"人均消费"）时，说明口径有歧义，输出 clarify 让用户选，不要直接判 unsupported。
9. 时间范围：整个区间都在数据范围之外（如 2019 年）时输出 unsupported_reason；用户明确说了"全年/整年"且该年被数据截止截断时才 clarify；只说某年、某季度、某月时，按该区间与数据覆盖的交集直接查询，不要澄清。
10. 当前只支持 month 粒度；用户要求按天/周/季度/年时，如果指标目录没有对应维度，输出 unsupported_reason，不要硬填 granularity。
11. limit 只有用户明确说 TopN/前 N/最近 N 个月时才填整数（1-1000），否则输出 null。TopN 指标不填 limit 时由契约的 default_limit 决定。

输出 JSON 结构：
{
  "metrics": ["指标id"],
  "dimensions": ["维度id"],
  "filters": [{"field": "维度id", "op": "=|!=|>|>=|<|<=|in|between", "value": 任意}],
  "time_range": {"start": "YYYY-MM-DD 或 null", "end": "YYYY-MM-DD 或 null", "granularity": "month", "compare": "none"},
  "order_by": [{"field": "指标id 或维度id", "desc": true}],
  "limit": null,
  "clarify": null,
  "unsupported_reason": null
}
只输出 JSON，不要任何解释文字。"""


def _catalog_text() -> str:
    lines = []
    for item in registry.describe_metrics():
        dims = "、".join(item["dimensions"]) or "无"
        filters = "、".join(item["filters"]) or "无"
        default_limit = item.get("default_limit") or "未指定"
        line = (
            f"- {item['id']}（{item['label']}，单位 {item['unit']}）：{item['note']}；"
            f"允许维度：{dims}；允许过滤：{filters}；默认条数：{default_limit}"
        )
        if item["clarifications"]:
            line += "；口径提醒：" + "；".join(item["clarifications"])
        lines.append(line)
    return "\n".join(lines)


def _dimension_text() -> str:
    lines = []
    for key, dim in registry.get_dimensions().items():
        line = f"- {key}（{dim['label']}）"
        examples = dim.get("examples") or {}
        if examples:
            line += "；取值必须用代码：" + "、".join(f"{code}={name}" for code, name in examples.items())
        lines.append(line)
    return "\n".join(lines)


def build_messages(question: str, data_range: tuple[str | None, str | None]) -> list[dict]:
    start, end = data_range
    user = f"""数据覆盖区间：{start} 至 {end}

可用指标：
{_catalog_text()}

可用维度：
{_dimension_text()}

用户问题：{question}"""
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user},
    ]


def _usage_dict(usage: Any) -> dict[str, int] | None:
    """只保留可核对的 token 字段；供应商没返回 usage 时明确返回 None。"""
    if usage is None:
        return None
    return {
        "prompt_tokens": int(getattr(usage, "prompt_tokens", 0) or 0),
        "completion_tokens": int(getattr(usage, "completion_tokens", 0) or 0),
        "total_tokens": int(getattr(usage, "total_tokens", 0) or 0),
    }


def _merge_usage(
    left: dict[str, int] | None, right: dict[str, int] | None
) -> dict[str, int] | None:
    """修复计划时可能调用两次 LLM，token 要累加而不是只留最后一次。"""
    if left is None:
        return right
    if right is None:
        return left
    return {
        key: left.get(key, 0) + right.get(key, 0)
        for key in ("prompt_tokens", "completion_tokens", "total_tokens")
    }


def _complete(client: OpenAI, messages: list[dict]) -> tuple[str, dict[str, int] | None]:
    resp = client.chat.completions.create(
        model=LLM_MODEL,
        messages=messages,
        temperature=0,
        response_format={"type": "json_object"},
        timeout=60,
    )
    return resp.choices[0].message.content or "{}", _usage_dict(getattr(resp, "usage", None))


def _to_plan(content: str) -> QueryPlan:
    try:
        return QueryPlan.model_validate(json.loads(content))
    except json.JSONDecodeError as exc:
        raise AppError("BAD_LLM_JSON", f"模型输出不是合法 JSON：{exc}") from exc
    except ValidationError as exc:
        raise AppError("BAD_LLM_PLAN", f"模型输出未通过计划校验：{exc}") from exc


def parse_plan(
    question: str, data_range: tuple[str | None, str | None]
) -> tuple[QueryPlan, dict[str, int] | None]:
    """调 DeepSeek 拿 QueryPlan；输出不合法时按约束 5 只修 1 次，不无限重试。"""
    if not DEEPSEEK_API_KEY:
        raise AppError("NO_API_KEY", "环境变量 DEEPSEEK_API_KEY 未设置")

    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    messages = build_messages(question, data_range)
    content, usage = _complete(client, messages)

    try:
        return _to_plan(content), usage
    except AppError as first:
        # 把具体的校验报错原样回给模型，比重新描述需求更准，也更省 token
        repair = messages + [
            {"role": "assistant", "content": content},
            {"role": "user", "content": f"上面的 JSON 不合格：{first.message}\n请修正后重新输出完整 JSON，不要输出解释。"},
        ]
        repaired_usage = None
        try:
            repaired_content, repaired_usage = _complete(client, repair)
            return _to_plan(repaired_content), _merge_usage(usage, repaired_usage)
        except AppError as second:
            raise AppError(
                "BAD_LLM_PLAN",
                f"模型输出修 1 次后仍不合格：{second.message}",
                usage=_merge_usage(usage, repaired_usage),
            ) from second
