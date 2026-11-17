"""SQL 静态校验：解析 + 语句类型 + 表名 + 函数名。

为什么需要这一层：编译器是"我们自己的代码"，但它读的是 YAML 契约，
契约写错或以后被人改坏，同样会生成危险 SQL。这一层是最后一道门。
注意：它只做静态检查，真正的隔离靠 DuckDB 只读连接（见 exec/duck.py）。
"""
from __future__ import annotations

import sqlglot
from sqlglot import exp

from src.config import AppError

# 只允许契约里出现过的表；CTE 名单独放行
ALLOWED_TABLES = {
    "orders",
    "order_items",
    "products",
    "customers",
    "order_payments",
    "order_reviews",
}

# 白名单而非黑名单：编译器只会用到这些函数，多出来的就说明有人改坏了
# 注意：sqlglot 会把部分函数归一化（strftime → time_to_str，date_diff → datediff，
# CASE → case 节点，IF → if），所以这里同时写原始名与归一化名
ALLOWED_FUNCTIONS = {
    "sum", "count", "avg", "min", "max",
    "coalesce", "nullif", "cast", "try_cast", "round", "abs",
    "date_diff", "datediff", "strftime", "time_to_str",
    "lag", "row_number", "date_trunc", "extract", "str_to_time",
    "generate_series", "range",
    "case", "if", "or", "and", "not", "is",
}

# 显式禁止的节点类型（DuckDB 方言里能写文件/改结构的语句）
FORBIDDEN_NODE_NAMES = {
    "Insert", "Update", "Delete", "Create", "Drop", "Alter",
    "Copy", "Attach", "Detach", "Pragma", "Command", "Set", "Use",
    "TruncateTable", "Export", "LoadData",
}


def _forbidden_node_types() -> tuple[type, ...]:
    found = []
    for name in FORBIDDEN_NODE_NAMES:
        node = getattr(exp, name, None)
        if isinstance(node, type):
            found.append(node)
    return tuple(found)


def check_sql(sql: str) -> str:
    """校验通过返回规范化后的 SQL；不通过抛 AppError。"""
    if not sql or not sql.strip():
        raise AppError("EMPTY_SQL", "生成的 SQL 为空")

    try:
        statements = sqlglot.parse(sql, dialect="duckdb")
    except Exception as exc:
        raise AppError("SQL_PARSE_ERROR", f"SQL 解析失败：{exc}") from exc

    statements = [s for s in statements if s is not None]
    if len(statements) != 1:
        # 多语句是典型的注入手法，一律拒绝
        raise AppError("MULTI_STATEMENT", "只允许单条语句")

    tree = statements[0]

    if not isinstance(tree, (exp.Select, exp.Subquery, exp.Union)):
        raise AppError("NOT_SELECT", f"只允许 SELECT，收到：{type(tree).__name__}")

    forbidden = _forbidden_node_types()
    for node in tree.walk():
        if isinstance(node, forbidden):
            raise AppError("FORBIDDEN_STATEMENT", f"包含被禁止的语句类型：{type(node).__name__}")

    # CTE 名不算真实表，先收集出来放行
    cte_names = {cte.alias_or_name.lower() for cte in tree.find_all(exp.CTE)}

    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        if not name:
            continue
        if name in cte_names:
            continue
        if name not in ALLOWED_TABLES:
            raise AppError("TABLE_NOT_ALLOWED", f"不允许访问的表：{name}")

    for func in tree.find_all(exp.Func):
        name = (func.sql_name() or "").lower()
        if name and name not in ALLOWED_FUNCTIONS:
            raise AppError("FUNCTION_NOT_ALLOWED", f"不允许使用的函数：{name}")

    return tree.sql(dialect="duckdb")
