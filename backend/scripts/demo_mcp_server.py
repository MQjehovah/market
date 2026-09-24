"""MCP HTTP 中转网关演示服务（MCPServer stdio）。

在管理后台「MCP 网关」注册本脚本即可把工具通过 HTTP 暴露给外部 MCP 客户端：
    transport = stdio
    command   = python
    args      = ["/app/backend/scripts/demo_mcp_server.py"]
"""

import sys

sys.stdout.reconfigure(encoding="utf-8")

from mcp.server.mcpserver import MCPServer  # noqa: E402

mcp = MCPServer("demo-mcp")


@mcp.tool()
def add(a: int, b: int) -> int:
    """两数相加"""
    return a + b


@mcp.tool()
def echo(text: str) -> str:
    """回显输入文本"""
    return "echo:" + text


if __name__ == "__main__":
    mcp.run()
