"""只读统计 logs/trace.jsonl：延迟、状态、错误码、阶段耗时和 token。

为什么单独做成脚本：报告里的数字必须能从原始 trace 复算，
不能把一次手工观察写进文档后就再也对不上。
"""
from __future__ import annotations

import argparse
import json
import math
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TRACE = ROOT / "logs" / "trace.jsonl"


def _percentile(values: list[float], percent: float) -> float | None:
    """线性插值百分位；空样本返回 None，避免报告里出现假的 0。"""
    if not values:
        return None
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * percent / 100
    low = math.floor(position)
    high = math.ceil(position)
    if low == high:
        return float(ordered[low])
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def _numbers(records: list[dict[str, Any]], key: str, positive_only: bool = False) -> list[float]:
    values = []
    for record in records:
        value = record.get(key)
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if not positive_only or value > 0:
                values.append(float(value))
    return values


def _latency_stats(records: list[dict[str, Any]], key: str, positive_only: bool = True) -> dict[str, Any]:
    values = _numbers(records, key, positive_only=positive_only)
    if not values:
        return {"n": 0, "p50_ms": None, "p95_ms": None, "max_ms": None, "mean_ms": None}
    return {
        "n": len(values),
        "p50_ms": round(_percentile(values, 50) or 0, 1),
        "p95_ms": round(_percentile(values, 95) or 0, 1),
        "max_ms": round(max(values), 1),
        "mean_ms": round(sum(values) / len(values), 1),
    }


def _usage_stats(
    records: list[dict[str, Any]],
    input_price_per_1m: float | None = None,
    output_price_per_1m: float | None = None,
) -> dict[str, Any] | None:
    usages = [record["usage"] for record in records if isinstance(record.get("usage"), dict)]
    if not usages:
        return None
    count = len(usages)
    avg_prompt = sum(int(item.get("prompt_tokens", 0) or 0) for item in usages) / count
    avg_completion = sum(int(item.get("completion_tokens", 0) or 0) for item in usages) / count
    stats = {
        "n": count,
        "avg_prompt_tokens": round(avg_prompt, 1),
        "avg_completion_tokens": round(avg_completion, 1),
        "avg_total_tokens": round(sum(int(item.get("total_tokens", 0) or 0) for item in usages) / count, 1),
    }
    if input_price_per_1m is not None and output_price_per_1m is not None:
        stats["estimated_cost_per_query"] = round(
            avg_prompt / 1_000_000 * input_price_per_1m
            + avg_completion / 1_000_000 * output_price_per_1m,
            6,
        )
    return stats


def _parse_bound(value: str | None, end_of_day: bool) -> datetime | None:
    """解析 --since / --until；只给日期时，until 取当天 23:59:59。"""
    if not value:
        return None
    parsed = datetime.fromisoformat(value)
    # trace.ts 是本地 naive 时间；带时区的输入先去掉 tzinfo，再按同一时钟比较。
    if parsed.tzinfo is not None:
        parsed = parsed.replace(tzinfo=None)
    if len(value) == 10 and end_of_day:
        parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def _filter_time(
    records: list[dict[str, Any]],
    since: datetime | None,
    until: datetime | None,
) -> list[dict[str, Any]]:
    if since is None and until is None:
        return records
    filtered = []
    for record in records:
        ts = record.get("ts")
        if not isinstance(ts, str):
            continue
        try:
            parsed = datetime.fromisoformat(ts)
        except ValueError:
            continue
        if parsed.tzinfo is not None:
            parsed = parsed.replace(tzinfo=None)
        if since is not None and parsed < since:
            continue
        if until is not None and parsed > until:
            continue
        filtered.append(record)
    return filtered


def _load(path: Path) -> tuple[list[dict[str, Any]], int]:
    if not path.exists():
        raise FileNotFoundError(path)
    records: list[dict[str, Any]] = []
    malformed = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        if isinstance(item, dict):
            records.append(item)
        else:
            malformed += 1
    return records, malformed


def build_report(
    records: list[dict[str, Any]],
    malformed: int,
    path: Path,
    input_price_per_1m: float | None = None,
    output_price_per_1m: float | None = None,
    last: int | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> dict[str, Any]:
    statuses = Counter(str(record.get("status") or "unknown") for record in records)
    errors = Counter(str(record["error_code"]) for record in records if record.get("error_code"))
    p95_by_status = {}
    for status in statuses:
        matched = [record for record in records if str(record.get("status") or "unknown") == status]
        stats = _latency_stats(matched, "elapsed_ms")
        p95_by_status[status] = {"n": stats["n"], "p95_ms": stats["p95_ms"]}

    timestamps = [str(record["ts"]) for record in records if record.get("ts")]
    return {
        "path": str(path),
        "n": len(records),
        "malformed": malformed,
        "malformed_scope": "file",
        "query": {
            "last": last,
            "since": since.isoformat() if since else None,
            "until": until.isoformat() if until else None,
        },
        "time_range": {
            "first": timestamps[0] if timestamps else None,
            "last": timestamps[-1] if timestamps else None,
        },
        "latency_ms": _latency_stats(records, "elapsed_ms"),
        "status": dict(sorted(statuses.items(), key=lambda item: (-item[1], item[0]))),
        "p95_by_status": p95_by_status,
        "error_code_top": [
            {"error_code": code, "n": count}
            for code, count in errors.most_common(5)
        ],
        "stage_ms": {
            "llm": _latency_stats(records, "llm_ms"),
            "sql": _latency_stats(records, "sql_ms"),
        },
        "usage": _usage_stats(records, input_price_per_1m, output_price_per_1m),
    }


def _print_report(report: dict[str, Any]) -> None:
    latency = report["latency_ms"]
    print(f"trace: {report['path']}")
    query = report.get("query") or {}
    if any(query.get(key) is not None for key in ("last", "since", "until")):
        print(
            f"过滤: last={query.get('last')} "
            f"since={query.get('since')} until={query.get('until')}"
        )
    print(
        f"样本: n={report['n']} malformed={report['malformed']}(全文件) "
        f"时间={report['time_range']['first']} ~ {report['time_range']['last']}"
    )
    print(
        f"延迟(ms): P50={latency['p50_ms']} P95={latency['p95_ms']} "
        f"max={latency['max_ms']} mean={latency['mean_ms']} (n={latency['n']})"
    )
    print("状态: " + " | ".join(f"{status}={count}" for status, count in report["status"].items()))
    print(
        "P95 by status: "
        + " | ".join(
            f"{status}={item['p95_ms']}ms(n={item['n']})"
            for status, item in sorted(report["p95_by_status"].items())
        )
    )
    top_errors = report["error_code_top"]
    print("error_code Top5: " + (" | ".join(f"{item['error_code']}={item['n']}" for item in top_errors) or "无"))
    llm = report["stage_ms"]["llm"]
    sql = report["stage_ms"]["sql"]
    print(
        f"阶段(ms): llm P95={llm['p95_ms']} (n={llm['n']}) | "
        f"sql P95={sql['p95_ms']} (n={sql['n']})"
    )
    usage = report["usage"]
    if usage:
        print(
            f"usage: avg prompt={usage['avg_prompt_tokens']} "
            f"completion={usage['avg_completion_tokens']} total={usage['avg_total_tokens']} "
            f"(n={usage['n']})"
        )
        if "estimated_cost_per_query" in usage:
            print(f"估算成本: {usage['estimated_cost_per_query']}/次（按传入单价）")
    else:
        print("usage: 无（旧 trace 未记录 token）")


def main() -> int:
    parser = argparse.ArgumentParser(description="只读统计 trace.jsonl")
    parser.add_argument("--path", default=str(DEFAULT_TRACE), help="trace 文件路径")
    parser.add_argument("--last", type=int, default=None, help="只统计最后 N 条")
    parser.add_argument("--since", default=None, help="起始时间（含），ISO 格式，如 2026-11-08 或 2026-11-08T18:46:58")
    parser.add_argument("--until", default=None, help="结束时间（含），ISO 格式；只给日期时取当天 23:59:59")
    parser.add_argument("--json", action="store_true", help="输出 JSON")
    parser.add_argument("--input-price-per-1m", type=float, default=None, help="输入单价 / 1M tokens；与输出单价同时提供才估算")
    parser.add_argument("--output-price-per-1m", type=float, default=None, help="输出单价 / 1M tokens；与输入单价同时提供才估算")
    args = parser.parse_args()
    if args.last is not None and args.last < 1:
        parser.error("--last 必须是正整数")
    if (args.input_price_per_1m is None) != (args.output_price_per_1m is None):
        parser.error("--input-price-per-1m 和 --output-price-per-1m 必须同时提供")
    try:
        since = _parse_bound(args.since, end_of_day=False)
        until = _parse_bound(args.until, end_of_day=True)
    except ValueError as exc:
        parser.error(f"时间格式不合法：{exc}")
    if since is not None and until is not None and since > until:
        parser.error("--since 不能晚于 --until")

    path = Path(args.path)
    try:
        records, malformed = _load(path)
    except FileNotFoundError:
        print(f"trace 不存在: {path}")
        return 1

    records = _filter_time(records, since, until)
    if args.last is not None:
        records = records[-args.last:]

    report = build_report(
        records,
        malformed,
        path,
        args.input_price_per_1m,
        args.output_price_per_1m,
        last=args.last,
        since=since,
        until=until,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    else:
        _print_report(report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
