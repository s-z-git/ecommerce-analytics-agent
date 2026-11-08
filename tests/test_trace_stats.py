"""trace_stats 的时间过滤、统计结构和 malformed 口径测试。"""
from __future__ import annotations

import importlib.util
from datetime import datetime
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "trace_stats.py"
SPEC = importlib.util.spec_from_file_location("trace_stats", MODULE_PATH)
assert SPEC and SPEC.loader
trace_stats = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(trace_stats)


def test_parse_bound_strips_timezone() -> None:
    parsed = trace_stats._parse_bound("2026-11-08T18:46:58+08:00", end_of_day=False)
    assert parsed == datetime(2026, 11, 8, 18, 46, 58)


def test_parse_bound_date_only_until_is_end_of_day() -> None:
    parsed = trace_stats._parse_bound("2026-11-08", end_of_day=True)
    assert parsed == datetime(2026, 11, 8, 23, 59, 59, 999999)


def test_filter_time_accepts_aware_bounds() -> None:
    records = [
        {"ts": "2026-11-08T18:46:58"},
        {"ts": "2026-11-08T18:47:00"},
        {"ts": "2026-11-08T18:48:00"},
    ]
    since = trace_stats._parse_bound("2026-11-08T18:46:58+08:00", end_of_day=False)
    until = trace_stats._parse_bound("2026-11-08T18:47:00+08:00", end_of_day=False)
    filtered = trace_stats._filter_time(records, since, until)
    assert [record["ts"] for record in filtered] == [
        "2026-11-08T18:46:58",
        "2026-11-08T18:47:00",
    ]


def test_build_report_records_malformed_scope_and_usage() -> None:
    records = [
        {
            "ts": "2026-11-08T18:46:58",
            "status": "ok",
            "elapsed_ms": 100,
            "llm_ms": 80,
            "sql_ms": 10,
            "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
        },
        {
            "ts": "2026-11-08T18:46:59",
            "status": "clarify",
            "elapsed_ms": 200,
            "llm_ms": 180,
        },
    ]
    report = trace_stats.build_report(records, malformed=3, path=Path("trace.jsonl"))
    assert report["n"] == 2
    assert report["malformed"] == 3
    assert report["malformed_scope"] == "file"
    assert report["latency_ms"]["n"] == 2
    assert report["status"]["ok"] == 1
    assert report["status"]["clarify"] == 1
    assert report["stage_ms"]["llm"]["n"] == 2
    assert report["stage_ms"]["sql"]["n"] == 1
    assert report["usage"]["n"] == 1
    assert report["usage"]["avg_total_tokens"] == 15.0
