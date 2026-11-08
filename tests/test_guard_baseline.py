"""基线测试：证明 guard 真的挡得住三类危险输入。"""
import pytest

from src.config import AppError
from src.exec import guard
from src.plan.schema import QueryPlan
from src.semantic import compiler


def test_blocks_drop():
    with pytest.raises(AppError) as exc:
        guard.check_sql("DROP TABLE orders")
    assert exc.value.code in ("NOT_SELECT", "FORBIDDEN_STATEMENT")


def test_blocks_multi_statement():
    with pytest.raises(AppError) as exc:
        guard.check_sql("SELECT 1; SELECT 2")
    assert exc.value.code == "MULTI_STATEMENT"


def test_blocks_forbidden_table():
    with pytest.raises(AppError) as exc:
        guard.check_sql("SELECT * FROM secret_table")
    assert exc.value.code == "TABLE_NOT_ALLOWED"


def test_compiler_emits_select_with_limit():
    plan = QueryPlan(metrics=["gmv"], dimensions=["month"], limit=10)
    compiled = compiler.compile_plan(plan)
    assert compiled.sql.strip().upper().startswith("WITH")
    assert "LIMIT ?" in compiled.sql
    assert compiled.params[-1] == 10


def test_compiler_output_passes_guard():
    plan = QueryPlan(metrics=["gmv"], dimensions=["month"], limit=10)
    compiled = compiler.compile_plan(plan)
    guard.check_sql(compiled.sql)
