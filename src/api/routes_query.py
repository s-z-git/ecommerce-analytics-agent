"""
POST /query 接口 —— 用户自然语言查询的核心入口。

功能说明：
  接收用户的自然语言问题，调用 Agent Pipeline 进行语义理解、SQL 生成与执行，
  最终返回结构化的表格数据或交互式追问。

响应状态码规范：
  - ok          → 200  查询成功，返回表格数据 + 指标口径说明
  - clarify     → 200  需要用户进一步澄清问题（正常交互流程）
  - unsupported → 200  当前不支持该查询类型，返回原因说明
  - error       → 400/422/500  根据 error_code 映射到具体 HTTP 状态码

设计原则：
  HTTP 边界层只做三件事 —— 请求校验、状态码映射、响应组装。
  所有业务逻辑统一收敛在 pipeline.answer() 中，API 层不重复实现。
"""
from __future__ import annotations

from fastapi import APIRouter          # FastAPI 的路由分组工具
from fastapi.encoders import jsonable_encoder  # 将 Pydantic 模型转为可 JSON 序列化的 dict
from fastapi.responses import JSONResponse    # 自定义 JSON 响应（可控制状态码）
from pydantic import BaseModel, Field, field_validator  # 请求体数据校验模型

from src.agent import pipeline       # 导入 Agent 核心管线（语义理解 → SQL → 执行）

# 创建路由实例，后续通过 app.include_router() 注册到主应用
router = APIRouter()


# ---------------------------------------------------------------------------
# 请求模型
# ---------------------------------------------------------------------------
class QueryRequest(BaseModel):
    """查询请求体，仅包含用户的自然语言问题。"""
    question: str = Field(
        min_length=1,       # 问题不能为空
        max_length=500      # 限制长度，防止滥用
    )

    @field_validator("question")
    @classmethod
    def strip_question(cls, value: str) -> str:
        """纯空白问题不应该触发一次 LLM 调用。"""
        value = value.strip()
        if not value:
            raise ValueError("question 不能为空白")
        return value


# ---------------------------------------------------------------------------
# 错误码 → HTTP 状态码 映射表
# ---------------------------------------------------------------------------
# 400：客户端输入不合法（未知指标、未知维度、不支持的时间范围等）
# 422：LLM 返回的 JSON 格式异常 / SQL 解析失败（语义正确但结构不合法）
# 500：服务端配置或运行时错误（缺少 API Key、数据库未找到等）
_ERROR_STATUS = {
    "UNKNOWN_METRIC": 400,           # 指标不存在于数据字典中
    "UNKNOWN_DIMENSION": 400,        # 维度不存在于数据字典中
    "DIMENSION_NOT_ALLOWED": 400,    # 该维度不允许用于当前查询
    "MULTI_METRIC_UNSUPPORTED": 400, # 暂不支持多指标联合查询
    "TIME_NOT_SUPPORTED": 400,       # 时间范围或粒度不支持
    "TIME_OUT_OF_RANGE": 400,         # 时间范围完全超出数据覆盖区间
    "FILTER_NOT_ALLOWED": 400,        # 该指标不允许按这个字段过滤
    "INVALID_FILTER": 400,            # in/between 等过滤值形状不合法
    "BAD_LLM_JSON": 422,             # LLM 返回的 JSON 格式错误，无法解析
    "BAD_LLM_PLAN": 422,             # LLM 返回的 JSON 结构/字段不合法
    "SQL_PARSE_ERROR": 422,          # 生成的 SQL 语法或结构不合法
    "EMPTY_SQL": 422,                # LLM 未能生成有效的 SQL 语句
    "NO_API_KEY": 500,               # 未配置 LLM API Key
    "DB_NOT_FOUND": 500,             # 目标数据库文件未找到
    "CONTRACT_ERROR": 500,           # Pipeline 内部契约校验失败
    "UNEXPECTED": 500,               # 未预期的运行时异常
    "QUERY_TIMEOUT": 504,            # 查询超过执行层超时上限
}


# ---------------------------------------------------------------------------
# 响应体构造函数（内部辅助函数）
# ---------------------------------------------------------------------------
def _body(result: dict) -> dict:
    """
    将 pipeline.answer() 返回的 dict 转换为统一的 API 响应体格式。

    所有响应分支（成功 / 追问 / 不支持 / 异常）都强制携带 trace_id，
    便于在日志系统中按 trace_id 定位完整调用链路。

    参数:
        result: pipeline.answer() 的返回值字典

    返回:
        统一的 API 响应体字典
    """
    # 基础字段：状态、追踪ID、原始问题（所有分支共享）
    body = {
        "status": result["status"],
        "trace_id": result["trace_id"],
        "question": result.get("question", ""),
    }

    if result["status"] in ("ok", "no_data"):
        # 成功 / 无数据分支：都保留表格结构，便于前端展示空状态
        body.update({
            "columns": result.get("columns", []),       # 表格列名列表
            "rows": result.get("rows", []),             # 表格数据行（二维列表）
            "row_count": result.get("row_count", 0),    # 数据总行数
            "metric": result.get("metric", {}),         # 指标口径元信息
        })
        if result.get("message"):
            body["message"] = result["message"]
        # 如果查询过程中有非致命警告，一并返回
        if result.get("warning"):
            body["warning"] = result["warning"]

    elif result["status"] in ("clarify", "unsupported"):
        # clarify：需要用户进一步澄清问题 → 返回追问提示
        # unsupported：当前不支持该查询 → 返回原因说明
        # 注意：这两种情况属于正常交互流程，HTTP 状态码仍为 200
        body["message"] = result.get("message", "")

    else:
        # 异常分支（status == "error"）：返回错误码和错误描述
        body.update({
            "error_code": result.get("error_code", "UNEXPECTED"),
            "message": result.get("message", ""),
        })

    return body


# ---------------------------------------------------------------------------
# POST /query —— 核心查询接口
# ---------------------------------------------------------------------------
@router.post("/query")
def query(req: QueryRequest) -> JSONResponse:
    """
    接收用户自然语言查询，返回结构化数据或交互追问。

    处理流程：
      1. 调用 pipeline.answer() 进行语义解析、SQL 生成与执行
      2. 根据返回的 status 决定 HTTP 状态码
      3. 组装统一格式的 JSON 响应体

    参数:
        req: 包含 question 字段的 Pydantic 请求模型

    返回:
        JSONResponse：统一格式的 JSON 响应
    """
    # 调用 Agent 核心管线，传入用户问题
    result = pipeline.answer(req.question)
    status = result["status"]

    # ok / no_data / clarify / unsupported 均为正常业务结果，统一返回 200
    if status in ("ok", "no_data", "clarify", "unsupported"):
        return JSONResponse(
            status_code=200,
            content=jsonable_encoder(_body(result))
        )

    # 只有 error 状态才映射到 4xx 或 5xx；未注册的错误码兜底为 500
    error_code = result.get("error_code", "UNEXPECTED")
    return JSONResponse(
        status_code=_ERROR_STATUS.get(error_code, 500),
        content=jsonable_encoder(_body(result)),
    )