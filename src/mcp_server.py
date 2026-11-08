"""MCP Server：把指标目录和自然语言问答暴露给 Claude Desktop 等 MCP 客户端。

只暴露两个工具：
- list_metrics：人能看懂的指标清单（不暴露 SQL 模板）
- query：自然语言问题 → pipeline 的统一结果

为什么不暴露"执行 SQL"工具：MCP 是外部入口，一旦允许传 SQL，
前面的指标契约、guard 白名单、只读连接就都形同虚设。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 允许 `python src/mcp_server.py` 直接启动（Claude Desktop 就是这么配的）
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from mcp.server.mcpserver import MCPServer

from src.agent import pipeline
from src.semantic import registry

mcp = MCPServer(
    "ecommerce-analytics",
    instructions=(
        "电商经营分析助手。先用 list_metrics 看有哪些口径，再用 query 提问。"
        "所有数字由指标契约渲染的只读 SQL 计算，不是模型生成的。"
    ),
)


@mcp.tool()
def list_metrics() -> list[dict]:
    """列出可查询的经营指标：id、中文名、单位、允许维度、口径说明。"""
    return registry.describe_metrics()


@mcp.tool()
def query(question: str) -> dict:
    """用中文问一个经营问题，返回统一结构。

    status 取值：ok（有数据）/ no_data（无数据）/ clarify（需要确认口径）/ unsupported（不支持）/ error（执行错误）。
    """
    return pipeline.answer(question)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
