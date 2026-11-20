"""电商会话记忆：prev_plan 注入 + 回顾 + 不调 LLM。"""
from __future__ import annotations

from src.agent import pipeline
from src.exec.duck import QueryResult
from src.plan import llm
from src.plan.schema import QueryPlan
from src.ui.conversation import detect_recall, recall_answer, recent_questions
from src.ui.sessions import SessionStore


def _seed(mem: SessionStore, sid: str = "s1") -> None:
    for q, a in [("2018 年 GMV", "gmv 结果"), ("那按州呢", "按州结果")]:
        mem.add_turn(sid, "user", q)
        mem.add_turn(sid, "assistant", a, meta={"status": "ok", "plan": QueryPlan(metrics=["gmv"]).model_dump()})


def test_detect_recall_variants() -> None:
    assert detect_recall("我刚才问了什么？") == "last"
    assert detect_recall("我上一个问题是什么") == "last"
    assert detect_recall("我刚才都问了什么？") == "list"
    assert detect_recall("我第一个问题是什么") == "first"
    assert detect_recall("2018 年 GMV 是多少") is None


def test_build_messages_includes_prev_plan() -> None:
    prev = QueryPlan(metrics=["gmv"], dimensions=["state"])
    messages = llm.build_messages("那按州呢", ("2016-09-04", "2018-10-17"), prev)
    user = messages[-1]["content"]
    assert "上一轮查询计划" in user
    assert '"gmv"' in user
    assert "只修改用户明确改变的部分" in user
    assert "上个月" in user


def test_pipeline_passes_prev_plan(monkeypatch) -> None:
    captured: dict = {}

    def fake_parse(question, data_range, prev_plan=None):
        captured["prev"] = prev_plan
        return QueryPlan(metrics=["gmv"]), None

    monkeypatch.setattr(pipeline.llm, "parse_plan", fake_parse)
    monkeypatch.setattr(pipeline, "_data_range", lambda: ("2016-09-04", "2018-10-17"))
    monkeypatch.setattr(pipeline, "_trace", lambda record: None)
    monkeypatch.setattr(pipeline.duck, "run_sql",
                        lambda sql, params: QueryResult(columns=["metric_value"], rows=[(1.0,)], elapsed_ms=1))
    prev = QueryPlan(metrics=["gmv"])
    result = pipeline.answer("那按州呢", prev_plan=prev)
    assert captured["prev"] is prev
    assert result["prev_plan_used"] is True
    assert result["status"] == "ok"


def test_recent_questions_excludes_current(tmp_path) -> None:
    mem = SessionStore(tmp_path / "ui.db")
    _seed(mem)
    mem.add_turn("s1", "user", "我刚才问了什么？")
    qs = recent_questions(mem, "s1", current_question="我刚才问了什么？")
    assert qs == ["2018 年 GMV", "那按州呢"]


def test_recall_answer_last_list_first(tmp_path) -> None:
    mem = SessionStore(tmp_path / "ui.db")
    _seed(mem)
    assert "那按州呢" in recall_answer(mem, "s1", "我刚才问了什么？")["message"]
    assert "2018 年 GMV" in recall_answer(mem, "s1", "我第一个问题是什么？")["message"]
    listed = recall_answer(mem, "s1", "我刚才都问了什么？")["message"]
    assert "1. 2018 年 GMV" in listed and "2. 那按州呢" in listed


def test_last_plan_returns_latest_ok(tmp_path) -> None:
    mem = SessionStore(tmp_path / "ui.db")
    plan = QueryPlan(metrics=["gmv"], dimensions=["state"]).model_dump()
    mem.add_turn("s1", "user", "按州看 GMV")
    mem.add_turn("s1", "assistant", "ok", meta={"status": "ok", "plan": plan})
    mem.add_turn("s1", "user", "坏问题")
    mem.add_turn("s1", "assistant", "error", meta={"status": "error"})
    assert mem.last_plan("s1") == plan


def test_last_plan_none_without_plan(tmp_path) -> None:
    mem = SessionStore(tmp_path / "ui.db")
    mem.add_turn("s1", "user", "你好")
    mem.add_turn("s1", "assistant", "你好")
    assert mem.last_plan("s1") is None


def test_recall_answer_none_for_normal_question(tmp_path) -> None:
    mem = SessionStore(tmp_path / "ui.db")
    _seed(mem)
    assert recall_answer(mem, "s1", "2018 年 GMV 是多少") is None