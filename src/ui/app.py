"""Streamlit 演示页：先给答案，再给口径和过程。

设计原则：页面只做展示，所有判断都在 pipeline 里。
结果先写入 session_state，再统一渲染；这样切换 tab / expander 不会丢结果。
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

import streamlit as st

# 允许 `streamlit run src/ui/app.py` 时导入 src 包
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.agent import pipeline  # noqa: E402
from src.exec import duck  # noqa: E402
from src.ui import summary  # noqa: E402

DATA_START = "2016-09-04"

EXAMPLES = [
    {
        "question": "2018 年 1 月的 GMV 是多少",
        "caption": "返回一个数字 + 指标口径",
    },
    {
        "question": "2018 年上半年各州订单数",
        "caption": "返回 27 个州，柱状图看排名",
    },
    {
        "question": "销售额是多少",
        "caption": "先澄清「销售额」指哪个指标",
    },
    {
        "question": "帮我删掉所有订单",
        "caption": "演示只读拦截，不会执行删除",
    },
]


@st.cache_data(ttl=300)
def _as_of() -> str:
    """数据截止日从库里读，不写死——写死的日期一旦更新数据就会说谎。"""
    try:
        return duck.get_as_of() or "未知"
    except Exception:  # noqa: BLE001 - 页面标题不能因为数据库不可用而崩掉
        return "未知"


def _init_state() -> None:
    if "last_result" not in st.session_state:
        st.session_state["last_result"] = None
    if "last_question" not in st.session_state:
        st.session_state["last_question"] = ""


def _run_query(question: str) -> None:
    question = question.strip()
    if not question:
        return
    with st.spinner("正在解析问题并查询…"):
        result = pipeline.answer(question)
    # 先存结果，再由页面统一渲染；不要依赖按钮分支里的局部变量。
    st.session_state["last_result"] = result
    st.session_state["last_question"] = question


def _records(result: dict[str, Any]) -> list[dict[str, Any]]:
    columns = result.get("columns") or []
    rows = result.get("rows") or []
    return [dict(zip(columns, row)) for row in rows]


def _render_details(result: dict[str, Any]) -> None:
    metric = result.get("metric") or {}
    plan = result.get("plan")
    sql = result.get("sql")
    if not plan and not sql and not metric.get("note"):
        return

    with st.expander("它是怎么算出来的"):
        if metric.get("note"):
            st.caption(f"口径说明：{metric['note']}")
        plan_tab, sql_tab = st.tabs(["查询计划", "SQL"])
        with plan_tab:
            st.json(plan or {})
        with sql_tab:
            st.code(sql or "（本次没有生成 SQL）", language="sql")


def _render_ok(result: dict[str, Any]) -> None:
    st.markdown(f"### {summary.summarize(result)}")

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
            st.bar_chart(
                chart["data"],
                x=chart["x"],
                y=chart["y"],
                sort=False,
                horizontal=chart.get("horizontal", False),
                y_label=chart.get("y_label"),
            )
        else:
            st.line_chart(
                chart["data"],
                x=chart["x"],
                y=chart["y"],
                y_label=chart.get("y_label"),
            )
        if chart.get("caption"):
            st.caption(chart["caption"])

    st.caption(summary.badge_line(result))

    row_count = result.get("row_count", len(result.get("rows") or []))
    with st.expander(f"完整结果（{row_count} 行）"):
        st.dataframe(_records(result), hide_index=True, width="stretch")

    _render_details(result)


def _render_examples() -> None:
    st.markdown("#### 先试一个")
    st.caption("点按钮直接查询，不用再按一次「查询」。")
    for start in range(0, len(EXAMPLES), 2):
        columns = st.columns(2)
        for column, example in zip(columns, EXAMPLES[start:start + 2]):
            with column:
                if st.button(
                    example["question"],
                    key=f"example_{start}_{example['question']}",
                    width="stretch",
                ):
                    _run_query(example["question"])
                    st.rerun()
                st.caption(example["caption"])


def _render_assistant(result: dict[str, Any]) -> None:
    status = result.get("status")
    message = str(result.get("message") or "")

    if status == "ok":
        _render_ok(result)
    elif status == "clarify":
        st.info("需要确认口径：" + message)
        st.caption("你可以补充：时间范围 / 指标口径（GMV 还是支付金额）")
        _render_details(result)
    elif status == "unsupported":
        st.info("暂不支持：" + message)
        st.caption("你可以问：GMV、订单数、取消率、延迟率、复购率、支付占比")
        _render_details(result)
    elif status == "no_data":
        st.info(message or "查询没有返回数据。")
        st.caption(f"当前数据范围：{DATA_START} ~ {_as_of()}")
        _render_details(result)
    elif status == "error":
        st.error(f"[{result.get('error_code')}] {result.get('message')}")
        st.caption(f"trace_id：{result.get('trace_id', '未知')}")
        if st.button("重试", key=f"retry_{result.get('trace_id', 'unknown')}"):
            _run_query(st.session_state.get("last_question", ""))
            st.rerun()
        _render_details(result)
    else:
        st.info("这次查询没有返回可识别的状态，请重新提问。")

    if result.get("warning"):
        st.warning(result["warning"])


st.set_page_config(page_title="电商经营分析 Agent", layout="wide")
_init_state()

st.title("电商经营分析 Agent")
st.caption(
    f"数据：Olist 公开数据集（截至 {_as_of()}）｜"
    "LLM 只输出查询计划，SQL 由 12 条指标契约渲染，DuckDB 只读执行"
)

question = st.text_input(
    "问一个经营问题",
    placeholder="例如：2018 年上半年各州 GMV 是多少",
    key="question_input",
)
if st.button("查询", type="primary"):
    if question.strip():
        _run_query(question)
        st.rerun()
    else:
        st.caption("请先输入一个问题，或点下面的示例。")

result = st.session_state["last_result"]
if result is None:
    _render_examples()
else:
    last_question = st.session_state.get("last_question") or result.get("question", "")
    with st.chat_message("user"):
        st.markdown(last_question)
    with st.chat_message("assistant", avatar="📊"):
        _render_assistant(result)

