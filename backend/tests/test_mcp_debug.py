"""MCP 调试接口：真实连接能力包、发现工具、调用工具。"""

import io
import json
import sys
import zipfile

import pytest

from test_workflow import _publish_capability


def _mcp_zip(name: str) -> bytes:
    impl = (
        "from mcp.server.mcpserver import MCPServer\n"
        "mcp = MCPServer('test')\n"
        "\n"
        "@mcp.tool()\n"
        "def echo(text: str) -> str:\n"
        "    \"\"\"回显输入文本\"\"\"\n"
        "    return 'echo:' + text\n"
        "\n"
        "if __name__ == '__main__':\n"
        "    mcp.run()\n"
    )
    files = {
        "mcp.json": json.dumps({"name": name, "description": "测试 MCP"}).encode("utf-8"),
        "connection.json": json.dumps(
            {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["implementation/server.py"],
                "env": {},
            }
        ).encode("utf-8"),
        "tools.json": json.dumps({"tools": []}).encode("utf-8"),
        "security.json": json.dumps({"sandbox": False}).encode("utf-8"),
        "implementation/server.py": impl.encode("utf-8"),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_mcp_debug_connect_and_call(client, publisher_headers, admin_headers):
    name = "debug-mcp"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "mcp", _mcp_zip(name)
    )

    # 真实连接并发现工具
    r = await client.post(f"/api/runtime/mcp/{name}/connect", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True
    assert body["error"] == ""
    assert len(body["tools"]) == 1
    tool = body["tools"][0]
    assert tool["name"] == "mcp_debug_mcp_echo"
    assert "inputSchema" in tool

    # 调用该工具
    r = await client.post(
        f"/api/runtime/mcp/{name}/call",
        headers=admin_headers,
        json={"tool": tool["name"], "params": {"text": "hi"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "echo:hi"

    # 不存在的工具返回 404 并提示可用工具
    r = await client.post(
        f"/api/runtime/mcp/{name}/call",
        headers=admin_headers,
        json={"tool": "mcp_debug_mcp_nope", "params": {}},
    )
    assert r.status_code == 404
    assert "可用工具" in r.json()["detail"]
