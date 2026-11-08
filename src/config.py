"""项目配置：路径、模型、执行限制。

为什么要集中在一个文件：口径与安全的开关散落在各处时，演示时很容易讲错默认值。
这里所有"能调的东西"都只有一处来源。
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# 项目根目录：src/config.py 的上两级
ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _resolve_path(env_name: str, default: Path) -> Path:
    """相对路径按项目根目录解析，避免从别的 cwd 启动时找错库。"""
    raw = os.getenv(env_name)
    path = Path(raw) if raw else default
    return path if path.is_absolute() else ROOT / path


DB_PATH = _resolve_path("DB_PATH", ROOT / "data" / "warehouse" / "olist.duckdb")
RAW_DIR = _resolve_path("RAW_DIR", ROOT / "data" / "raw")
METRICS_PATH = ROOT / "src" / "semantic" / "metrics.yaml"

DEEPSEEK_API_KEY = os.getenv("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
LLM_MODEL = os.getenv("LLM_MODEL", "deepseek-flash")


@dataclass(frozen=True)
class Limits:
    """执行层上限。三个值都必须有，缺一个就能被"慢查询"拖死。"""

    timeout_seconds: int = int(os.getenv("SQL_TIMEOUT_SECONDS", "10"))
    max_rows: int = int(os.getenv("MAX_ROWS", "1000"))
    memory_limit: str = os.getenv("MEMORY_LIMIT", "2GB")


LIMITS = Limits()


class AppError(Exception):
    """带错误码的业务异常，API 层靠 code 决定 HTTP 状态。"""

    def __init__(
        self,
        code: str,
        message: str,
        usage: dict[str, int] | None = None,
    ):
        super().__init__(message)
        self.code = code
        self.message = message
        # 计划解析失败时也要把已经消耗的 token 带回 trace。
        self.usage = usage
