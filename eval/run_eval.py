"""评测：执行结果等价比较，不做 SQL 字符串比对。

为什么比结果不比 SQL：同一个问题有无数种等价写法（JOIN 顺序、别名、子查询），
比字符串会把正确回答判错，这样的评测没有意义。

--repeat N 会重复跑同一题集 N 轮，并输出每题的 pass_count / N。
总分只作为汇总，单题翻转单独列出，避免 90/90 掩盖某题 2/3。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.agent import pipeline  # noqa: E402
from src.exec import duck  # noqa: E402


def _values_equal(left: Any, right: Any) -> bool:
    """数值用相对/绝对容差；不要先 round(2)，否则 0.63% 和 1.24% 会被判成同一个值。"""
    if left is None or right is None:
        return left is None and right is None
    if isinstance(left, bool) or isinstance(right, bool):
        return left == right
    if isinstance(left, (int, float)) and isinstance(right, (int, float)):
        return math.isclose(float(left), float(right), rel_tol=1e-6, abs_tol=1e-9)
    return left == right


def _row_equal(left: tuple, right: tuple) -> bool:
    return len(left) == len(right) and all(_values_equal(a, b) for a, b in zip(left, right))


def _rows_equal(gold: list[tuple], actual: list[tuple], ordered: bool = False) -> bool:
    if len(gold) != len(actual):
        return False
    if ordered:
        return all(_row_equal(g, a) for g, a in zip(gold, actual))
    # 小结果集用贪心匹配；既忽略行序，又保留数值容差。
    unmatched = list(actual)
    for gold_row in gold:
        for idx, actual_row in enumerate(unmatched):
            if _row_equal(gold_row, actual_row):
                unmatched.pop(idx)
                break
        else:
            return False
    return True


def compare(gold_sql: str, actual_rows: list[list], ordered: bool = False) -> bool:
    try:
        gold = duck.run_sql(gold_sql, [])
    except Exception:  # noqa: BLE001 - gold SQL 坏了应该判失败，不应该让整个评测崩掉
        return False
    return _rows_equal(gold.rows, [tuple(r) for r in actual_rows], ordered=ordered)


def _judge(item: dict[str, Any], result: dict[str, Any]) -> bool:
    expect = item["expect"]
    ordered = bool(item.get("ordered", False))
    if expect == "answer":
        return result["status"] == "ok" and compare(
            item["gold_sql"], result["rows"], ordered=ordered
        )
    if expect in ("clarify", "unsupported", "no_data"):
        return result["status"] == expect
    return False


def _run_once(item: dict[str, Any]) -> dict[str, Any]:
    result = pipeline.answer(item["question"])
    return {
        "passed": _judge(item, result),
        "status": result.get("status"),
        "error_code": result.get("error_code"),
        "row_count": result.get("row_count"),
        "rows_sample": (result.get("rows") or [])[:5],
    }


def run(path: Path, repeat: int = 1) -> dict[str, Any]:
    items = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    per_question = []

    for item in items:
        runs = [_run_once(item) for _ in range(repeat)]
        pass_count = sum(1 for run in runs if run["passed"])
        per_question.append({
            "question": item["question"],
            "expect": item["expect"],
            "pass_count": pass_count,
            "runs": runs,
        })

    total_runs = len(items) * repeat
    total_passed = sum(question["pass_count"] for question in per_question)
    all_pass = sum(1 for question in per_question if question["pass_count"] == repeat)
    n_minus_1 = (
        sum(1 for question in per_question if question["pass_count"] == repeat - 1)
        if repeat > 1 else 0
    )
    flips = [
        question for question in per_question
        if 0 < question["pass_count"] < repeat
    ]

    report = {
        "file": str(path),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "repeat": repeat,
        "total_questions": len(items),
        "total_runs": total_runs,
        "total_passed": total_passed,
        "all_pass_questions": all_pass,
        "n_minus_1_questions": n_minus_1,
        "flips": flips,
        "per_question": per_question,
    }

    report_dir = ROOT / "eval" / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    report_path = report_dir / f"stability-{timestamp}.json"
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )
    report["report_path"] = str(report_path)
    return report


def _print_report(report: dict[str, Any]) -> None:
    repeat = report["repeat"]
    print(
        f"{Path(report['file']).name}: repeat={repeat} "
        f"passed={report['total_passed']}/{report['total_runs']}"
    )
    print(f"{repeat}/{repeat} 题数: {report['all_pass_questions']}")
    if repeat > 1:
        print(f"{repeat - 1}/{repeat} 题数: {report['n_minus_1_questions']}")
    print("每题:")
    for question in report["per_question"]:
        print(f"  {question['pass_count']}/{repeat}  {question['question']}")
    if report["flips"]:
        print("翻转题:")
        for question in report["flips"]:
            statuses = " | ".join(
                f"{run['status']}({run.get('error_code') or '-'})"
                for run in question["runs"]
            )
            print(f"  {question['pass_count']}/{repeat}  {question['question']}  ->  {statuses}")
    else:
        print("翻转题: 无")
    print(f"报告: {report['report_path']}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--file", default=str(ROOT / "eval" / "questions.dev.jsonl"))
    parser.add_argument("--repeat", type=int, default=1, help="同一题集重复跑 N 轮")
    args = parser.parse_args()
    if args.repeat < 1:
        parser.error("--repeat 必须是正整数")

    report = run(Path(args.file), repeat=args.repeat)
    _print_report(report)
    return 0 if report["total_passed"] == report["total_runs"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
