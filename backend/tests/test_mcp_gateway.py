"""MCP HTTP 中转网关测试：注册表 CRUD、stdio 探测、Streamable HTTP / SSE 入站。"""

import asyncio
import io
import json
import sys
import zipfile

import httpx
import pytest
import uvicorn
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.streamable_http import streamable_http_client

from app.main import app
from app.services.mcp_gateway import connect_upstream
from test_workflow import _publish_capability

FASTMCP_SERVER = r"""
import sys
sys.stdout.reconfigure(encoding="utf-8")
from mcp.server.fastmcp import FastMCP

mcp = FastMCP("demo-mcp")

@mcp.tool()
def add(a: int, b: int) -> int:
    # add two integers
    return a + b

@mcp.tool()
def echo(text: str) -> str:
    # echo text
    return "echo:" + text

mcp.run()
"""


@pytest.fixture
def mcp_script(tmp_path):
    p = tmp_path / "demo_server.py"
    p.write_text(FASTMCP_SERVER, encoding="utf-8")
    return str(p)


def _payload(name, script, token="gw-test-token", **kw):
    return {
        "name": name,
        "description": "测试网关服务",
        "transport": "stdio",
        "command": sys.executable,
        "args": [script],
        "env": {},
        "api_token": token,
        "enabled": True,
        **kw,
    }


@pytest.mark.asyncio
async def test_gateway_admin_crud_forbidden(client, admin_headers, user_headers, mcp_script):
    # 非管理员不可管理网关
    r = await client.get("/api/admin/mcp-gateway/servers", headers=user_headers)
    assert r.status_code == 403

    payload = _payload("crud-mcp", mcp_script)
    r = await client.post("/api/admin/mcp-gateway/servers", headers=admin_headers, json=payload)
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    # 重名冲突
    r = await client.post("/api/admin/mcp-gateway/servers", headers=admin_headers, json=payload)
    assert r.status_code == 409

    r = await client.get("/api/admin/mcp-gateway/servers", headers=admin_headers)
    assert r.status_code == 200
    assert [s["name"] for s in r.json()] == ["crud-mcp"]

    # 更新
    payload["description"] = "更新后的描述"
    payload["api_token"] = "new-token"
    r = await client.put(
        f"/api/admin/mcp-gateway/servers/{sid}", headers=admin_headers, json=payload
    )
    assert r.status_code == 200, r.text
    assert r.json()["api_token"] == "new-token"

    # 删除
    r = await client.delete(f"/api/admin/mcp-gateway/servers/{sid}", headers=admin_headers)
    assert r.status_code == 200
    r = await client.get("/api/admin/mcp-gateway/servers", headers=admin_headers)
    assert r.json() == []


@pytest.mark.asyncio
async def test_gateway_probe_and_call(client, admin_headers, mcp_script):
    r = await client.post(
        "/api/admin/mcp-gateway/servers",
        headers=admin_headers,
        json=_payload("probe-mcp", mcp_script),
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    r = await client.post(
        f"/api/admin/mcp-gateway/servers/{sid}/test", headers=admin_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True, body
    assert {t["name"] for t in body["tools"]} == {"add", "echo"}

    # 直接使用出站连接器调用工具
    config = {
        "name": "probe-mcp",
        "transport": "stdio",
        "command": sys.executable,
        "args": [mcp_script],
        "env": {},
    }
    async with connect_upstream(config) as session:
        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == {"add", "echo"}
        result = await session.call_tool("add", {"a": 3, "b": 4})
    assert "7" in (result.content[0].text if result.content else "")


@pytest.mark.asyncio
async def test_stdio_preserves_implementation_tree(tmp_path):
    """嵌套 implementation/pkg/server.py 应保留目录，并按相对路径启动。"""
    nested = tmp_path / "pkg"
    nested.mkdir()
    script = nested / "server.py"
    script.write_text(
        "from mcp.server.fastmcp import FastMCP\n"
        "mcp = FastMCP('nested')\n"
        "@mcp.tool()\n"
        "def ping() -> str:\n"
        "    return 'pong'\n"
        "mcp.run()\n",
        encoding="utf-8",
    )
    files = {"implementation/pkg/server.py": script.read_bytes()}
    config = {
        "name": "nested-mcp",
        "transport": "stdio",
        "command": "python",
        "args": ["implementation/pkg/server.py"],
        "env": {},
    }
    async with connect_upstream(config, files) as session:
        tools = await session.list_tools()
        assert {t.name for t in tools.tools} == {"ping"}


@pytest.mark.asyncio
async def test_stdio_connect_surfaces_stderr(tmp_path):
    """子进程 import 失败时，错误信息应带上 stderr，而不是只剩 TaskGroup。"""
    script = tmp_path / "broken.py"
    script.write_text("import definitely_missing_module_xyz\n", encoding="utf-8")
    config = {
        "name": "broken-mcp",
        "transport": "stdio",
        "command": "python",
        "args": [str(script)],
        "env": {},
    }
    with pytest.raises(RuntimeError) as ei:
        async with connect_upstream(config) as _session:
            pass
    msg = str(ei.value)
    assert "definitely_missing_module_xyz" in msg
    assert "TaskGroup" not in msg or "definitely_missing_module_xyz" in msg


async def _start_uvicorn():
    config = uvicorn.Config(app, host="127.0.0.1", port=0, log_level="error")
    server = uvicorn.Server(config)
    task = asyncio.create_task(server.serve())
    for _ in range(100):
        if server.started and server.servers:
            break
        await asyncio.sleep(0.05)
    port = server.servers[0].sockets[0].getsockname()[1]
    return server, task, port


async def _stop_uvicorn(server, task):
    server.should_exit = True
    try:
        await asyncio.wait_for(task, timeout=10)
    except (asyncio.TimeoutError, Exception):  # noqa: BLE001
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass


@pytest.mark.asyncio
async def test_gateway_streamable_http_inbound(client, admin_headers, mcp_script):
    r = await client.post(
        "/api/admin/mcp-gateway/servers",
        headers=admin_headers,
        json=_payload("stream-mcp", mcp_script),
    )
    assert r.status_code == 201, r.text

    server, task, port = await _start_uvicorn()
    try:
        base = f"http://127.0.0.1:{port}"
        gw_url = f"{base}/api/mcp-gateway/stream-mcp/stream"
        headers = {"X-Gateway-Token": "gw-test-token"}

        # 无令牌 → 401
        async with httpx.AsyncClient(timeout=30) as c:
            resp = await c.post(gw_url, json={})
        assert resp.status_code == 401

        # 官方 SDK 客户端走 Streamable HTTP 调用
        async with httpx.AsyncClient(headers=headers, timeout=60) as c:
            async with streamable_http_client(gw_url, http_client=c) as streams:
                async with ClientSession(streams[0], streams[1]) as session:
                    await session.initialize()
                    tools = await session.list_tools()
                    assert {t.name for t in tools.tools} == {"add", "echo"}
                    result = await session.call_tool("add", {"a": 2, "b": 5})
        assert "7" in (result.content[0].text if result.content else "")

        # 出站方向：connect_upstream 通过 HTTP 连回网关
        cfg = {
            "name": "out",
            "transport": "streamable_http",
            "url": gw_url,
            "headers": {"X-Gateway-Token": "gw-test-token"},
        }
        async with connect_upstream(cfg) as session:
            tools = await session.list_tools()
        assert {t.name for t in tools.tools} == {"add", "echo"}
    finally:
        await _stop_uvicorn(server, task)


@pytest.mark.asyncio
async def test_gateway_sse_inbound(client, admin_headers, mcp_script):
    r = await client.post(
        "/api/admin/mcp-gateway/servers",
        headers=admin_headers,
        json=_payload("sse-mcp", mcp_script),
    )
    assert r.status_code == 201, r.text

    server, task, port = await _start_uvicorn()
    try:
        headers = {"X-Gateway-Token": "gw-test-token"}
        async with sse_client(
            f"http://127.0.0.1:{port}/api/mcp-gateway/sse-mcp/sse",
            headers=headers,
            timeout=10,
            sse_read_timeout=60,
        ) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                tools = await session.list_tools()
                result = await session.call_tool("echo", {"text": "hi"})
        assert {t.name for t in tools.tools} == {"add", "echo"}
        assert "echo:hi" in (result.content[0].text if result.content else "")
    finally:
        await _stop_uvicorn(server, task)


def _mcp_gateway_zip(name: str, server: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("mcp.json", json.dumps({"name": name, "description": "通过网关接入"}).encode("utf-8"))
        zf.writestr(
            "connection.json",
            json.dumps({"transport": "gateway", "server": server}).encode("utf-8"),
        )
        zf.writestr("tools.json", json.dumps({"tools": []}).encode("utf-8"))
        zf.writestr("security.json", json.dumps({"sandbox": False}).encode("utf-8"))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_mcp_bridge_gateway_transport(
    client, publisher_headers, admin_headers, mcp_script
):
    """MCP 能力包 transport=gateway：Agent 运行时/调试通过网关注册表连接。"""
    r = await client.post(
        "/api/admin/mcp-gateway/servers",
        headers=admin_headers,
        json=_payload("bridge-mcp", mcp_script, token="bridge-token"),
    )
    assert r.status_code == 201, r.text

    name = "gateway-cap"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "mcp", _mcp_gateway_zip(name, "bridge-mcp")
    )

    r = await client.post(f"/api/runtime/mcp/{name}/connect", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True, body
    assert {t["name"] for t in body["tools"]} == {"mcp_gateway_cap_add", "mcp_gateway_cap_echo"}

    tool = next(t for t in body["tools"] if t["name"].endswith("_add"))
    r = await client.post(
        f"/api/runtime/mcp/{name}/call",
        headers=admin_headers,
        json={"tool": tool["name"], "params": {"a": 6, "b": 7}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"] == "13"
