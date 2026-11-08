"""`--repeat` 的计数与退出码测试；不调用真实 LLM。"""
from __future__ import annotations

import json
import sys
from collections import defaultdict

import pytest

from eval import run_eval


def test_repeat_reports_per_question_counts_and_flips(monkeypatch, tmp_path) -> None:
    path = tmp_path / "questions.jsonl"
    items = [
        {"question": "q1", "expect": "clarify"},
        {"question": "q2", "expect": "clarify"},
    ]
    path.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in items), encoding="utf-8")

    statuses = {
        "q1": ["clarify", "clarify"],
        "q2": ["clarify", "unsupported"],
    }
    seen: dict[str, int] = defaultdict(int)

    def fake_answer(question: str) -> dict:
        index = seen[question]
        seen[question] += 1
        return {"status": statuses[question][index], "rows": []}

    monkeypatch.setattr(run_eval.pipeline, "answer", fake_answer)
    monkeypatch.setattr(run_eval, "ROOT", tmp_path)

    report = run_eval.run(path, repeat=2)

    assert report["total_runs"] == 4
    assert report["total_passed"] == 3
    assert report["all_pass_questions"] == 1
    assert report["n_minus_1_questions"] == 1
    assert len(report["flips"]) == 1
    assert report["flips"][0]["question"] == "q2"
    assert report["flips"][0]["pass_count"] == 1
    assert report["per_question"][0]["pass_count"] == 2
    assert report["per_question"][1]["pass_count"] == 1
    assert (tmp_path / "eval" / "reports").exists()


@pytest.mark.parametrize(
    ("passed", "expected"),
    [(2, 0), (1, 1)],
)
def test_main_returns_nonzero_when_not_all_runs_pass(monkeypatch, passed: int, expected: int) -> None:
    monkeypatch.setattr(sys, "argv", ["run_eval.py", "--file", "unused.jsonl"])
    monkeypatch.setattr(
        run_eval,
        "run",
        lambda path, repeat: {"total_passed": passed, "total_runs": 2},
    )
    monkeypatch.setattr(run_eval, "_print_report", lambda report: None)
    assert run_eval.main() == expected
