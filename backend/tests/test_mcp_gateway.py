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
from conftest import MCP_V1_AVAILABLE
from test_workflow import _publish_capability

# 本文件用真实 MCP SDK 拉起 stdio/SSE 服务：需要 mcp<2（FastMCP）。
# 本地开发环境若装了 mcp>=2，跳过而不是失败；容器/CI 用 pinned 版本执行。
pytestmark = pytest.mark.skipif(
    not MCP_V1_AVAILABLE,
    reason="需要 mcp<2（本地为 mcp>=2；容器/CI 用 pinned 版本）",
)

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
    from app.main import gateway_registry

    await gateway_registry.shutdown()


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


@pytest.mark.asyncio
async def test_capability_gateway_requires_bearer_and_runtime(
    client, publisher_headers, admin_headers, user_headers
):
    from test_mcp_debug import _mcp_zip
    from app.services.mcp_gateway import authorize_capability_gateway

    name = "cap-gw-auth"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "mcp", _mcp_zip(name)
    )

    def scope(headers: dict[str, str] | None = None):
        raw = []
        for k, v in (headers or {}).items():
            raw.append((k.lower().encode("latin-1"), v.encode("latin-1")))
        return {
            "type": "http",
            "asgi": {"version": "3.0"},
            "method": "GET",
            "scheme": "http",
            "path": f"/api/mcp-gateway/relay/{name}/sse",
            "raw_path": f"/api/mcp-gateway/relay/{name}/sse".encode(),
            "root_path": "",
            "query_string": b"",
            "headers": raw,
            "client": ("127.0.0.1", 1),
            "server": ("test", 80),
        }

    cfg, status, body = await authorize_capability_gateway(scope(), name)
    assert status == 401
    assert cfg is None
    assert "Bearer" in (body or {}).get("detail", "")

    cfg, status, _body = await authorize_capability_gateway(scope(user_headers), name)
    assert status == 403
    assert cfg is None

    cfg, status, _body = await authorize_capability_gateway(scope(admin_headers), name)
    assert status is None
    assert cfg is not None
    assert cfg.get("package_files")

    cfg, status, _body = await authorize_capability_gateway(scope(admin_headers), "不存在的mcp")
    assert status == 404


@pytest.mark.asyncio
async def test_capability_gateway_upstream_from_published_package(
    client, publisher_headers, admin_headers
):
    """能力级网关与 runtime 共用 connection.json + implementation 解包，不经 uvicorn。"""
    from test_mcp_debug import _mcp_zip
    from app.services.mcp_gateway import authorize_capability_gateway, connect_upstream

    name = "cap-gw-up"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "mcp", _mcp_zip(name)
    )
    scope = {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "GET",
        "scheme": "http",
        "path": f"/api/mcp-gateway/relay/{name}/sse",
        "raw_path": f"/api/mcp-gateway/relay/{name}/sse".encode(),
        "root_path": "",
        "query_string": b"",
        "headers": [
            (b"authorization", admin_headers["Authorization"].encode("latin-1"))
        ],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }
    cfg, status, _body = await authorize_capability_gateway(scope, name)
    assert status is None and cfg is not None
    async with connect_upstream(cfg) as session:
        tools = await session.list_tools()
        result = await session.call_tool("echo", {"text": "desk"})
    assert "echo" in {t.name for t in tools.tools}
    assert "echo:desk" in (result.content[0].text if result.content else "")

    # 能力级主入口 /relay：未登录 401；旧 /cap 别名已移除 404
    r = await asyncio.wait_for(client.get(f"/api/mcp-gateway/relay/{name}/sse"), 8)
    assert r.status_code == 401, "relay"
    r = await asyncio.wait_for(client.get(f"/api/mcp-gateway/cap/{name}/sse"), 8)
    assert r.status_code == 404, "cap alias removed"


@pytest.mark.asyncio
async def test_upstream_session_survives_beyond_connect_timeout(mcp_script, monkeypatch):
    """回归：连接超时不得包住会话存活期。

    曾把 30s 连接超时误当会话寿命（asyncio.timeout 包住 yield），超时后上游被杀、
    网关会话崩溃，客户端后续 tools/call 全部 Session not found。
    """
    import app.services.mcp_gateway as gw

    monkeypatch.setattr(gw, "CONNECT_TIMEOUT", 1.0)
    config = {
        "name": "keepalive",
        "transport": "stdio",
        "command": sys.executable,
        "args": [mcp_script],
    }
    async with gw.connect_upstream(config) as session:
        await asyncio.sleep(1.6)  # 超过 CONNECT_TIMEOUT，旧实现此时上游已被杀掉
        result = await session.call_tool("echo", {"text": "alive"})
    text = result.content[0].text if result.content else ""
    assert "echo:alive" in text
