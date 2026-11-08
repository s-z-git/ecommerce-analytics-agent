"""DuckDB 执行：只读连接 + 资源上限 + 超时中断。"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any

import duckdb

from src.config import DB_PATH, LIMITS, AppError


@dataclass
class QueryResult:
    columns: list[str]
    rows: list[tuple]
    elapsed_ms: int


def _connect() -> duckdb.DuckDBPyConnection:
    if not DB_PATH.exists():
        raise AppError("DB_NOT_FOUND", f"数据库不存在：{DB_PATH}，请先跑 scripts/build_db.py")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    # 内存上限防止一条大聚合把机器拖死
    con.execute(f"SET memory_limit='{LIMITS.memory_limit}'")
    return con


def _interrupt(con: duckdb.DuckDBPyConnection) -> None:
    try:
        con.interrupt()
    except Exception:  # noqa: BLE001 - 连接可能已经正常关闭
        pass


def _start_timeout(con: duckdb.DuckDBPyConnection) -> threading.Timer | None:
    if LIMITS.timeout_seconds <= 0:
        return None
    timer = threading.Timer(LIMITS.timeout_seconds, _interrupt, args=(con,))
    timer.daemon = True
    timer.start()
    return timer


def run_sql(sql: str, params: list[Any], max_rows: int | None = None) -> QueryResult:
    """执行已通过 guard 校验的 SQL。"""
    limit = max_rows or LIMITS.max_rows
    start = time.perf_counter()

    con = _connect()
    timer = _start_timeout(con)
    try:
        cursor = con.execute(sql, params)
        columns = [d[0] for d in cursor.description] if cursor.description else []
        rows = cursor.fetchmany(limit + 1)
    except duckdb.InterruptException as exc:
        raise AppError(
            "QUERY_TIMEOUT",
            f"查询超过 {LIMITS.timeout_seconds}s，已被中断",
        ) from exc
    finally:
        if timer is not None:
            timer.cancel()
        # 连接必须关：DuckDB 只读连接也持有文件锁，泄漏会导致下次打不开
        con.close()

    elapsed = int((time.perf_counter() - start) * 1000)
    if len(rows) > limit:
        rows = rows[:limit]

    return QueryResult(columns=columns, rows=rows, elapsed_ms=elapsed)


def get_as_of() -> str | None:
    """数据截止日期：所有"最近/本月"都要锚定它，不能用 today()。"""
    from src.exec import guard
    from src.semantic import registry

    safe_sql = guard.check_sql(registry.as_of_sql())
    con = _connect()
    timer = _start_timeout(con)
    try:
        row = con.execute(safe_sql).fetchone()
    except duckdb.InterruptException as exc:
        raise AppError(
            "QUERY_TIMEOUT",
            f"数据截止日期查询超过 {LIMITS.timeout_seconds}s，已被中断",
        ) from exc
    finally:
        if timer is not None:
            timer.cancel()
        con.close()
    if not row or row[0] is None:
        return None
    return str(row[0])[:10]
