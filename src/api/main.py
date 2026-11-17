"""FastAPI 入口。只负责 HTTP 边界：错误码 → 状态码，请求 → 业务。"""
from __future__ import annotations

from fastapi import FastAPI

from src.api.routes_query import router

app = FastAPI(
    title="电商经营分析 Agent",
    description="受约束 NL2SQL：LLM 只出 QueryPlan，SQL 由指标契约渲染",
    version="0.1.0",
)
app.include_router(router)


@app.get("/health")
def health() -> dict:
    """健康检查：演示时先打开这个接口确认服务活着。"""
    from src.config import DB_PATH

    return {
        "status": "ok",
        "db_exists": DB_PATH.exists(),
    }
