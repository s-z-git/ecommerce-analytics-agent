"""安全用例 —— 8 条攻击场景，全部必须被拦截。"""
import pytest

from src.config import AppError
from src.exec import guard
from src.plan.schema import QueryPlan, TimeRange
from src.semantic import compiler


def assert_blocked(sql: str) -> str:
    """断言 SQL 被 guard 拦截，并返回错误码。"""
    with pytest.raises(AppError) as excinfo:
        guard.check_sql(sql)
    return excinfo.value.code


def test_ddl_drop_table():
    """攻击：让模型/用户删表。期望：被语句类型白名单拦住。"""
    assert assert_blocked("DROP TABLE orders") in ("NOT_SELECT", "FORBIDDEN_STATEMENT")


def test_read_local_file():
    """攻击：用 DuckDB 的 read_csv 读本机文件。期望：函数白名单拦住。

    思考题：如果 read_csv 写在子查询里，静态检查还拦得住吗？运行时靠什么兜底？
    """
    assert assert_blocked("SELECT * FROM read_csv('C:/Windows/win.ini')") == "FUNCTION_NOT_ALLOWED"


def test_multi_statement_injection():
    """攻击：用分号拼接第二条语句。期望：多语句被拒。"""
    assert assert_blocked("SELECT 1; DROP TABLE orders") == "MULTI_STATEMENT"


def test_attach_database():
    """攻击：ATTACH 一个外部数据库，绕过表白名单。"""
    assert assert_blocked("ATTACH 'other.db' AS x") in ("NOT_SELECT", "FORBIDDEN_STATEMENT")


def test_copy_to_file():
    """攻击：把查询结果写成本地文件（数据外泄）。"""
    assert assert_blocked("COPY (SELECT 1) TO 'out.csv'") in ("NOT_SELECT", "FORBIDDEN_STATEMENT")


def test_unknown_table():
    """攻击：访问契约外的表。"""
    assert assert_blocked("SELECT * FROM information_schema.tables") == "TABLE_NOT_ALLOWED"


def test_forbidden_function_in_subquery():
    """攻击：把危险函数藏在子查询里，看看静态检查是否能发现。

    提示：guard 遍历的是整棵树，不是只看最外层。
    """
    assert assert_blocked("SELECT * FROM (SELECT * FROM read_parquet('x.parquet'))") == "FUNCTION_NOT_ALLOWED"


def test_huge_result_has_limit():
    """攻击：不写 LIMIT 想拉全库。

    这条不测 guard，而是测编译结果：任何 plan 生成的 SQL 都必须带 LIMIT。
    提示：用 compiler.compile_plan 生成再断言。
    """
    plan = QueryPlan(
        metrics=["gmv"],
        time_range=TimeRange(start="2018-01-01", end="2018-06-30"),
        limit=50,
    )
    compiled = compiler.compile_plan(plan)
    # 编译器最后一行固定是 LIMIT ?；参数列表最后一个就是 limit
    assert compiled.sql.rstrip().upper().endswith("LIMIT ?")
    assert compiled.params[-1] == 50
