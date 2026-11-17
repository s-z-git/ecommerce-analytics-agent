"""GMV 环比：完整月份、缺月、limit 的回归测试。"""
from __future__ import annotations

import pytest

from src.config import DB_PATH
from src.exec import duck, guard
from src.plan.schema import QueryPlan, TimeRange
from src.semantic import compiler

pytestmark = pytest.mark.skipif(not DB_PATH.exists(), reason="需要先跑 scripts/build_db.py")


def _rows(plan: QueryPlan):
    compiled = compiler.compile_plan(plan)
    return compiled, duck.run_sql(guard.check_sql(compiled.sql), compiled.params).rows


def test_full_range_only_complete_months() -> None:
    _compiled, rows = _rows(QueryPlan(metrics=["gmv_mom"]))
    assert rows[0][0] == "2017-01"
    assert rows[-1][0] == "2018-08"
    assert len(rows) == 20
    assert all(row[0] not in {"2016-09", "2016-10", "2016-12", "2018-09", "2018-10"} for row in rows)


def test_partial_boundary_months_are_trimmed() -> None:
    compiled, rows = _rows(QueryPlan(
        metrics=["gmv_mom"],
        time_range=TimeRange(start="2017-01-15", end="2017-09-01"),
    ))
    assert compiled.params[:2] == ["2017-02-01", "2017-08-31"]
    assert rows[0][0] == "2017-02"
    assert rows[-1][0] == "2017-08"


def test_limit_returns_latest_months() -> None:
    _compiled, rows = _rows(QueryPlan(
        metrics=["gmv_mom"],
        time_range=TimeRange(start="2017-01-01", end="2017-09-30"),
        limit=3,
    ))
    assert [row[0] for row in rows] == ["2017-07", "2017-08", "2017-09"]


def test_gap_month_does_not_produce_fake_mom() -> None:
    # 2016-11 没有订单；新的 date spine 只会输出完整月份窗口，
    # 因此 2016-12 不应再拿 2016-10 作为 prev_v。
    _compiled, rows = _rows(QueryPlan(metrics=["gmv_mom"]))
    assert all(row[0] >= "2017-01" for row in rows)
