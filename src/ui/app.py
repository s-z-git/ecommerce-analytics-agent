"""电商经营分析 Agent —— Streamlit 页面。

结构：左侧会话 → 中间“口径 → 结论 → KPI → 图 → 明细” → 底部追问。
只做展示；所有判断仍在 pipeline 里。每次查询语义独立，追问需补全条件。
"""
from __future__ import annotations

import html
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agent import pipeline  # noqa: E402
from src.exec import duck  # noqa: E402
from src.ui import summary  # noqa: E402
from src.ui.session_list import render_session_list  # noqa: E402
from src.ui.sessions import SessionStore  # noqa: E402

DATA_START = "2016-09-04"

EXAMPLES = [
    {"question": "2018 年 1 月的 GMV 是多少", "caption": "单指标 · 一个数字"},
    {"question": "2018 年上半年各州订单数", "caption": "分组排名 · 柱状图"},
    {"question": "销售额是多少", "caption": "口径不清 · 先澄清"},
    {"question": "帮我删掉所有订单", "caption": "只读拦截 · 不执行"},
]

CSS = """
<style>
.stApp { background: #ffffff; }
.block-container { max-width: 920px; padding-top: 1.2rem; padding-bottom: 7rem; }
#MainMenu, footer, header [data-testid="stToolbar"] { visibility: hidden; height: 0; }
[data-testid="stSidebar"] { background: #FAFBFC; border-right: 1px solid #E5E7EB; width: 280px !important; }
[data-testid="stSidebar"] > div:first-child { padding-top: .8rem; }
html, body, .stMarkdown, p, li, h1, h2, h3, button, input, textarea {
    font-family: -apple-system, BlinkMacSystemFont, "PingFang SC", "Microsoft YaHei",
                 "Noto Sans SC", sans-serif;
}
.ans-title { font-size: 1.55rem; font-weight: 700; letter-spacing: -.01em;
             color: #111827; line-height: 1.2; }
.ans-sub { color: #6B7280; font-size: .82rem; margin-top: .3rem; }
.ans-q { margin: 1.1rem 0 .7rem auto; max-width: 76%; width: fit-content;
         background: #F3F4F6; color: #111827; padding: .55rem .8rem;
         border-radius: 10px; font-size: .92rem; line-height: 1.6; }
.ans-sep { border-top: 1px solid #F1F2F4; margin: .4rem 0 1.1rem; }
.ans-conclusion { font-size: 1.16rem; font-weight: 650; line-height: 1.62;
                  color: #111827; padding: .15rem 0 .55rem; }
.ans-kicker { color: #9CA3AF; font-size: .72rem; letter-spacing: .06em;
              margin: .25rem 0 .1rem; }
.ans-foot { color: #9CA3AF; font-size: .72rem; margin-top: .6rem; }
.state-card { border-radius: 8px; padding: .7rem .85rem; font-size: .86rem;
              line-height: 1.7; margin: .2rem 0 .5rem; }
.state-info { background: #F5F8FF; border: 1px solid #DCE6FB; color: #1E3A8A; }
.state-warn { background: #FFFBEB; border: 1px solid #F0E2B6; color: #8A5A00; }
.state-error { background: #FEF2F2; border: 1px solid #F5D0CE; color: #991B1B; }
.side-label { color: #9CA3AF; font-size: .68rem; letter-spacing: .08em;
              margin: .7rem 0 .2rem .15rem; }
.side-time { color: #9CA3AF; font-size: .66rem; margin: .35rem 0 -.3rem .2rem; }
.side-group { color: #6B7280; font-size: .66rem; font-weight: 600;
              margin: .7rem 0 .05rem .2rem; }
[data-testid="stSidebar"] .stButton button { text-align: left; justify-content: flex-start;
    border: 0; background: transparent; padding: .2rem .35rem; font-size: .8rem;
    min-height: 1.7rem; color: #374151; }
[data-testid="stSidebar"] .stButton button:hover { background: #EEF2F7; color: #111827; }
[data-testid="stSidebar"] .stButton button p { font-size: .8rem !important; text-align: left; width: 100%; }
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stPopover"] button {
    min-height: 1.5rem; height: 1.5rem; width: 1.6rem; padding: 0; color: #9CA3AF;
    border: 0; background: transparent; }
[data-testid="stSidebar"] [data-testid="stHorizontalBlock"] [data-testid="stPopover"] button:hover {
    background: #EEF2F7; color: #111827; }
[data-testid="stSidebar"] [data-testid="stPopover"] button { min-height: 1.9rem; }
[data-testid="stMain"] .stButton button { min-height: 2.3rem; }
[data-testid="stMain"] [data-testid="stPopover"] button { font-size: .7rem;
    min-height: 1.6rem; padding: 0 .45rem; color: #9CA3AF; }
[data-testid="stChatInput"] { border-radius: 12px; }
</style>
"""


@st.cache_data(ttl=300)
def _as_of() -> str:
    """数据截止日从库里读，不写死。"""
    try:
        return duck.get_as_of() or "未知"
    except Exception:  # noqa: BLE001 - 页面标题不能因为数据库不可用而崩掉
        return "未知"


def _records(result: dict[str, Any]) -> list[dict[str, Any]]:
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    return [dict(zip(columns, row)) for row in rows]


def resolve_session(memory: SessionStore) -> str:
    """URL 里的 ?s= 优先；首问才落库，只打开页面不产生空会话。"""
    raw = st.query_params.get("s")
    session_id = raw if isinstance(raw, str) else (raw[0] if raw else "")
    if not session_id or not memory.session_exists(session_id):
        session_id = uuid.uuid4().hex[:8]
        st.query_params["s"] = session_id
    return session_id


def render_question(question: str) -> None:
    st.markdown('<div class="ans-q">' + html.escape(question) + "</div>",
                unsafe_allow_html=True)


def render_state_card(kind: str, title: str, body: str) -> None:
    cls = {"info": "state-info", "warn": "state-warn", "error": "state-error"}.get(kind, "state-info")
    st.markdown('<div class="state-card %s"><b>%s</b><br>%s</div>'
                % (cls, html.escape(title), html.escape(body)), unsafe_allow_html=True)


def render_footer(result: dict[str, Any]) -> None:
    line = summary.badge_line(result)
    left, right = st.columns([0.86, 0.14], vertical_alignment="center")
    with left:
        if line:
            st.markdown('<div class="ans-foot">' + html.escape(line) + "</div>",
                        unsafe_allow_html=True)
    with right:
        with st.popover("trace", width="content"):
            st.code(str(result.get("trace_id") or "无"))


def render_ok(result: dict[str, Any]) -> None:
    metric = result.get("metric") or {}
    bits = []
    if metric.get("label") or metric.get("id"):
        bits.append("口径 " + str(metric.get("label") or metric.get("id")))
    if metric.get("unit"):
        bits.append(str(metric.get("unit")))
    bits.append("只读")
    st.caption("数据截至 %s · %s" % (_as_of(), " · ".join(bits)))
    if result.get("warning"):
        st.warning(str(result["warning"]))

    st.markdown('<div class="ans-conclusion">'
                + html.escape(summary.summarize(result)) + "</div>",
                unsafe_allow_html=True)

    cards = summary.kpi_cards(result)
    if cards:
        columns = st.columns(len(cards))
        for column, (label, value, sub) in zip(columns, cards):
            with column:
                st.metric(label, value)
                if sub:
                    st.caption(sub)

    chart = summary.chart_spec(result)
    if chart:
        if chart["kind"] == "bar":
            st.bar_chart(chart["data"], x=chart["x"], y=chart["y"], sort=False,
                         horizontal=chart.get("horizontal", False),
                         y_label=chart.get("y_label"))
        else:
            st.line_chart(chart["data"], x=chart["x"], y=chart["y"],
                          y_label=chart.get("y_label"))
        if chart.get("caption"):
            st.caption(chart["caption"])
    elif (result.get("row_count") or len(result.get("rows") or [])) > 1:
        st.caption("本次结果不适合画图，完整数据见下方。")

    row_count = result.get("row_count", len(result.get("rows") or []))
    with st.expander("完整结果（%s 行）" % row_count):
        st.dataframe(_records(result), hide_index=True, width="stretch")

    with st.expander("计算口径与 SQL"):
        if metric.get("note"):
            st.caption("口径说明：" + str(metric["note"]))
        plan_tab, sql_tab = st.tabs(["查询计划", "SQL"])
        with plan_tab:
            st.json(result.get("plan") or {})
        with sql_tab:
            st.code(result.get("sql") or "（本次没有生成 SQL）", language="sql")

    render_footer(result)


def render_clarify(result: dict[str, Any]) -> None:
    question = str(result.get("question") or "")
    message = str(result.get("message") or "这句话对应多个指标，请选择一种口径。")
    render_state_card("warn", "需要确认口径", message)
    st.caption("选一个口径直接重跑：")
    options = [
        ("按 GMV 口径", question + "（按 GMV 口径）"),
        ("按支付金额口径", question + "（按支付金额口径）"),
        ("按卖家收入口径", question + "（按卖家收入口径）"),
    ]
    columns = st.columns(3)
    for index, (label, candidate) in enumerate(options):
        with columns[index]:
            if st.button(label, key="clarify_%d" % index, width="stretch"):
                st.session_state["auto_submit"] = candidate
                st.rerun()


def render_unsupported(result: dict[str, Any]) -> None:
    message = str(result.get("message") or "当前只覆盖 GMV、订单、取消、延迟、复购等经营指标。")
    render_state_card("info", "暂不支持这个问题", message)
    st.caption("可以试试：")
    columns = st.columns(3)
    for index, example in enumerate(EXAMPLES[:3]):
        with columns[index]:
            if st.button(example["question"], key="unsupported_%d" % index, width="stretch"):
                st.session_state["auto_submit"] = example["question"]
                st.rerun()


def render_no_data(result: dict[str, Any]) -> None:
    render_state_card("info", "没有查到数据",
                      str(result.get("message") or "这个时间范围可能没有记录。"))
    st.caption("当前数据范围：%s ~ %s" % (DATA_START, _as_of()))


def render_error(result: dict[str, Any]) -> None:
    code = str(result.get("error_code") or "UNKNOWN")
    render_state_card("error", "这次查询没有完成",
                      "[%s] %s" % (code, str(result.get("message") or "未知错误")))
    st.caption("trace_id：" + str(result.get("trace_id") or "无"))
    if st.button("重试这次查询", type="primary", key="retry_query"):
        st.session_state["auto_submit"] = str(result.get("question") or "")
        st.rerun()


def render_answer(result: dict[str, Any]) -> None:
    status = result.get("status")
    if status == "ok":
        render_ok(result)
    elif status == "clarify":
        render_clarify(result)
    elif status == "unsupported":
        render_unsupported(result)
    elif status == "no_data":
        render_no_data(result)
    elif status == "error":
        render_error(result)
    else:
        render_state_card("info", "这次查询没有返回可识别的状态", "请重新提问。")


def render_empty_state() -> None:
    st.markdown('<div class="ans-kicker">可以这样问</div>', unsafe_allow_html=True)
    for start in range(0, len(EXAMPLES), 2):
        columns = st.columns(2)
        for offset, example in enumerate(EXAMPLES[start:start + 2]):
            with columns[offset]:
                with st.container(border=True):
                    if st.button(example["question"],
                                 key="example_%d" % (start + offset),
                                 type="tertiary", width="stretch"):
                        st.session_state["auto_submit"] = example["question"]
                        st.session_state["auto_from_example"] = True
                        st.rerun()
                    st.caption(example["caption"])


def pair_turns(messages: list[dict]) -> list[tuple[dict, dict | None]]:
    turns: list[tuple[dict, dict | None]] = []
    for message in messages:
        if message.get("role") == "user":
            turns.append((message, None))
        elif message.get("role") == "assistant" and turns and turns[-1][1] is None:
            turns[-1] = (turns[-1][0], message)
    return turns


def handle_question(memory: SessionStore, session_id: str, question: str) -> None:
    """写入用户消息 → 查询 → 写库 → rerun 统一回放。"""
    memory.add_turn(session_id, "user", question)
    st.session_state["scroll_to_latest"] = True
    render_question(question)
    with st.spinner("正在解析问题并查询…"):
        try:
            result = pipeline.answer(question)
        except Exception as exc:  # noqa: BLE001 - 页面必须给出可读错误
            result = {"status": "error", "question": question, "trace_id": "",
                      "error_code": "UI_EXCEPTION", "message": str(exc),
                      "elapsed_ms": None}
    if result.get("status") == "ok":
        text = summary.summarize(result)
    else:
        text = str(result.get("message") or result.get("status") or "查询未完成")
    memory.add_turn(session_id, "assistant", text, meta=result)
    st.rerun()


def main() -> None:
    st.set_page_config(page_title="电商经营分析 Agent", page_icon="📊", layout="wide")
    st.markdown(CSS, unsafe_allow_html=True)

    st.markdown('<div class="ans-title">电商经营分析 Agent</div>', unsafe_allow_html=True)
    st.markdown('<div class="ans-sub">数据截至 %s · SQL 由指标契约渲染 · DuckDB 只读</div>'
                % _as_of(), unsafe_allow_html=True)

    memory = SessionStore()
    session_id = resolve_session(memory)

    with st.sidebar:
        render_session_list(st, memory, session_id)
        if not os.getenv("DEEPSEEK_API_KEY"):
            st.warning("未配置 DEEPSEEK_API_KEY：提问会失败。")
        st.divider()
        with st.popover("⚙ 设置", width="stretch"):
            st.caption("数据范围：%s ~ %s" % (DATA_START, _as_of()))
            st.caption("执行：DuckDB 只读 · 指标契约 13 条")
            st.caption("每次查询独立；追问请补全时间与指标。")
        st.caption("会话 ID：" + session_id)

    messages = memory.get_messages(session_id)
    if not messages:
        render_empty_state()
    else:
        turns = pair_turns(messages)
        for index, (user_msg, assistant_msg) in enumerate(turns):
            if index:
                st.markdown('<div class="ans-sep"></div>', unsafe_allow_html=True)
            if index == len(turns) - 1 and assistant_msg is not None:
                st.html('<div id="latest-answer"></div>')
            render_question(str(user_msg.get("content") or ""))
            if assistant_msg is not None:
                meta = assistant_msg.get("meta") or {}
                if meta:
                    render_answer(meta)
                else:
                    st.markdown(str(assistant_msg.get("content") or ""))
        should_scroll = st.session_state.pop("scroll_to_latest", False)
        if should_scroll and len(turns) > 1:
            st.html("""
<script>
function scrollToLatest() {
  var box = document.querySelector('[data-testid="stAppScrollToBottomContainer"]');
  var el = document.getElementById('latest-answer');
  if (!box || !el) return;
  var top = el.getBoundingClientRect().top - box.getBoundingClientRect().top + box.scrollTop - 10;
  box.scrollTo({top: Math.max(0, top), behavior: 'instant'});
}
setTimeout(scrollToLatest, 250);
setTimeout(scrollToLatest, 900);
</script>
""", unsafe_allow_javascript=True)

    submitted = st.session_state.pop("auto_submit", "")
    typed = st.chat_input("问一个经营问题，例如：2018 年 1 月的 GMV 是多少")
    if typed:
        submitted = typed.strip()
    if str(submitted or "").strip():
        handle_question(memory, session_id, str(submitted).strip())


if __name__ == "__main__":
    main()


