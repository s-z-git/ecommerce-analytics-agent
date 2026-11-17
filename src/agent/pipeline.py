"""五步状态机：解析 → 校验 → 渲染 → 校验 → 执行。"""
from __future__ import annotations

import json
import time
import traceback
import uuid
from datetime import datetime
from typing import Any

from src.config import AppError, ROOT
from src.exec import duck, guard
from src.plan import llm
from src.plan.schema import QueryPlan
from src.semantic import compiler, registry

TRACE_PATH = ROOT / "logs" / "trace.jsonl"


def _trace(record: dict) -> None:
    TRACE_PATH.parent.mkdir(parents=True, exist_ok=True)
    record["ts"] = datetime.now().isoformat(timespec="seconds")
    with open(TRACE_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def _data_range() -> tuple[str | None, str | None]:
    try:
        as_of = duck.get_as_of()
    except AppError:
        return None, None
    # Olist 起始日期固定，这里写死是为了少一次全表扫描
    return "2016-09-04", as_of


def _all_metric_values_null(rows: list[tuple], columns: list[str], metric_column: str) -> bool:
    """标量聚合没有数据时会返回一行 NULL，不能只判断 len(rows)==0。"""
    if not rows:
        return True
    try:
        idx = columns.index(metric_column)
    except ValueError:
        return False
    return all(row[idx] is None for row in rows)


def _negative_metric_warning(rows: list[tuple], columns: list[str], metric_column: str) -> str | None:
    """sanity 检查只告警不改数；负的 GMV/订单数通常意味着口径或数据有问题。"""
    try:
        idx = columns.index(metric_column)
    except ValueError:
        return None
    for row in rows:
        value = row[idx]
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0:
            return "结果包含负值，请检查口径或数据质量。"
    return None


def answer(question: str, plan_override: QueryPlan | None = None) -> dict[str, Any]:
    """执行一次完整问答，返回统一结构的结果。失败也返回结构，不抛到上层。

    plan_override 只给离线回放/测试使用：传入后跳过 LLM，其余链路完全不变。
    """
    trace_id = uuid.uuid4().hex[:12]
    start = time.perf_counter()
    result: dict[str, Any] = {"trace_id": trace_id, "question": question, "status": "error"}
    error_detail: str | None = None
    llm_usage: dict[str, int] | None = None
    stage_ms: dict[str, int] = {}

    try:
        data_range = _data_range()
        if plan_override is None:
            llm_start = time.perf_counter()
            try:
                plan, llm_usage = llm.parse_plan(question, data_range)
            finally:
                stage_ms["llm_ms"] = int((time.perf_counter() - llm_start) * 1000)
        else:
            plan = plan_override
        result["plan"] = plan.model_dump()
        # 规范化只改形状不改变语义；留有差异时保留原始计划，便于排查模型行为
        normalized = registry.canonicalize(plan, data_range)
        if normalized.model_dump() != result["plan"]:
            result["plan_raw"] = result["plan"]
            result["plan"] = normalized.model_dump()
        plan = normalized

        if plan.clarify:
            result["status"] = "clarify"
            result["message"] = plan.clarify
            return result

        if plan.unsupported_reason:
            result["status"] = "unsupported"
            result["message"] = plan.unsupported_reason
            return result

        # 无时间字段的指标被要求按时间过滤：这是用户误解，不是非法输入，走澄清
        scope_clarify = registry.time_filter_clarify(plan)
        if scope_clarify:
            result["status"] = "clarify"
            result["message"] = scope_clarify
            return result

        # 确定性时间兜底：完全超出数据范围时拒答，不返回 ok + NULL
        scope_error = registry.time_scope_error(plan, data_range)
        if scope_error:
            result["status"] = "unsupported"
            result["message"] = scope_error
            return result
        scope_warning = registry.time_scope_warning(plan, data_range)

        sql_start = time.perf_counter()
        try:
            metric, _dims = registry.validate_plan(plan)
            compiled = compiler.compile_plan(plan)
            safe_sql = guard.check_sql(compiled.sql)
            result["sql"] = safe_sql

            qr = duck.run_sql(safe_sql, compiled.params)
        finally:
            stage_ms["sql_ms"] = int((time.perf_counter() - sql_start) * 1000)
        columns = [compiled.column_labels.get(c, c) for c in qr.columns]
        rows = [list(r) for r in qr.rows]
        result.update({
            "status": "ok",
            "columns": columns,
            "rows": rows,
            "row_count": len(rows),
            "metric": {
                "id": compiled.metric_id,
                "label": compiled.metric_label,
                "unit": compiled.unit,
                "note": compiled.note,
                "additivity": metric.get("additivity"),
            },
        })
        warnings = [scope_warning] if scope_warning else []
        negative_warning = _negative_metric_warning(qr.rows, qr.columns, compiled.metric_column)
        if negative_warning:
            warnings.append(negative_warning)
        if warnings:
            result["warning"] = "；".join(warnings)

        # 结果体检：空结果和全 NULL 都归为 no_data；不改数，只改状态。
        if _all_metric_values_null(qr.rows, qr.columns, compiled.metric_column):
            result["status"] = "no_data"
            result["message"] = "查询没有返回数据，可能是时间范围或过滤条件不匹配。"
            result["rows"] = []
            result["row_count"] = 0

    except AppError as exc:
        result["status"] = "error"
        result["error_code"] = exc.code
        result["message"] = exc.message
        llm_usage = getattr(exc, "usage", None) or llm_usage
    except Exception as exc:  # noqa: BLE001 - 兜底，保证 API 不会 500 堆栈外泄
        result["status"] = "error"
        result["error_code"] = "UNEXPECTED"
        result["message"] = "服务内部错误，请用 trace_id 查询日志"
        error_detail = traceback.format_exc()
    finally:
        result["elapsed_ms"] = int((time.perf_counter() - start) * 1000)
        metric_meta = result.get("metric") or {}
        _trace({
            "trace_id": trace_id,
            "question": question,
            "status": result.get("status"),
            "error_code": result.get("error_code"),
            "plan": result.get("plan"),
            "sql": result.get("sql"),
            "metric_id": metric_meta.get("id"),
            "row_count": result.get("row_count"),
            "warning": result.get("warning"),
            "error_detail": error_detail,
            "elapsed_ms": result["elapsed_ms"],
            "llm_ms": stage_ms.get("llm_ms"),
            "sql_ms": stage_ms.get("sql_ms"),
            "usage": llm_usage,
        })

    return result
