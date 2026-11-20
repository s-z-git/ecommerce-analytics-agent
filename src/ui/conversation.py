"""电商会话回顾：规则识别 + 确定性答案，不调 LLM。"""
from __future__ import annotations

import re

_PUNCT = re.compile(r"[\s，。！？、,.!?；;：:]+")
_LAST = (
    r"^(我)?(刚才|刚刚|上一条|上一次|上一轮)(问|说)(了)?(的)?(是)?(什么|啥)(问题)?$",
    r"^(我)?上(一)?个问题(是)?(什么|啥)$",
    r"^(我)?上一轮(问|说)(的)?(是)?(什么|啥)$",
)
_LIST = (
    r"^(我)?(刚才|刚刚|这轮|这次)(都)?问(了)?(哪些|什么)(问题)?$",
    r"^这(轮|次)会话(里|中)?(我)?(都)?问(了)?(哪些|什么)(问题)?$",
    r"^(我)?(都)?问过(哪些|什么)问题$",
)
_FIRST = (
    r"^(我)?(的)?第一个问题(是)?(什么|啥)$",
    r"^(我)?最(早|开始)(问|说)(的)?(是)?(什么|啥)$",
)


def detect_recall(question: str) -> str | None:
    q = _PUNCT.sub("", question or "")
    if not q:
        return None
    for kind, patterns in (("last", _LAST), ("list", _LIST), ("first", _FIRST)):
        if any(re.search(p, q) for p in patterns):
            return kind
    return None


def recent_questions(memory, session_id: str, limit: int = 5,
                     current_question: str | None = None) -> list[str]:
    """从 SessionStore 取最近 N 个用户问题，剔除 UI 已写入的当前问句。"""
    msgs = memory.get_messages(session_id)
    if (current_question and msgs and msgs[-1].get("role") == "user"
            and str(msgs[-1].get("content") or "").strip() == current_question.strip()):
        msgs = msgs[:-1]
    questions = [str(m.get("content") or "").strip()
                 for m in msgs if m.get("role") == "user"]
    return questions[-max(1, limit):]


def recall_answer(memory, session_id: str, question: str) -> dict | None:
    """回顾类问句返回统一 result；不是回顾类返回 None。"""
    kind = detect_recall(question)
    if not kind:
        return None
    questions = recent_questions(memory, session_id, limit=5, current_question=question)
    if not questions:
        message = "这是本轮对话的第一个问题，之前没有提问记录。"
    elif kind == "last":
        message = f"你上一个问的是：「{questions[-1]}」。"
    elif kind == "first":
        message = f"这一轮会话里你第一个问的是：「{questions[0]}」。"
    else:
        lines = [f"这一轮会话里你问了 {len(questions)} 个问题："]
        lines += [f"{i}. {q}" for i, q in enumerate(questions, 1)]
        message = "\n".join(lines)
    return {
        "status": "ok",
        "message": message,
        "answer": message,
        "answer_type": "session_recall",
        "recall_kind": kind,
        "history_used": True,
        "llm_calls": 0,
        "trace_id": "",
        "elapsed_ms": 0,
        "rows": [],
        "columns": [],
    }