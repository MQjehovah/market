"""MCP 网关治理测试：身份来源识别、路由判定、消息级审计与 /stream 超时。

说明：本机 mcp 2.x 的 StreamableHTTPSessionManager 与网关 _ServerProxy 不兼容
（基线既有失败），治理逻辑不依赖上游 SDK，因此用 stub 替换 handle_stream，
覆盖 ASGI 全链路的鉴权/审计/超时。
限流与熔断由 gateway_governance 承担，见 test_phase1_governance.py；本文件的
authenticate_request 仅用于识别审计身份来源，不再承担鉴权动作。
"""

import asyncio
import json

import pytest
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Capability, MCPGatewayCall, MCPGatewayServer, User
from app.services.mcp_gateway import (
    GatewayAuthError,
    GatewayEndpoints,
    authenticate_request,
    parse_mcp_calls,
)

URL = "/api/mcp-gateway/{name}/stream"


def _scope(headers: dict[str, str] | None = None, client=("127.0.0.1", 1234)) -> dict:
    return {
        "type": "http",
        "headers": [(k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in (headers or {}).items()],
        "client": client,
    }


def _config(token: str = "", capability_id: str = "") -> dict:
    return {"name": "gov-srv", "api_token": token, "capability_id": capability_id}


async def _insert_server(name: str, *, api_token: str = "", capability_id: str = "") -> None:
    async with SessionLocal() as db:
        db.add(
            MCPGatewayServer(
                name=name,
                description="治理测试",
                transport="stdio",
                command="python",
                args=[],
                env={},
                headers={},
                api_token=api_token,
                capability_id=capability_id,
                enabled=True,
            )
        )
        await db.commit()


async def _insert_capability(version: str = "1.2.3", status: str = "published") -> str:
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.username == "admin"))
        cap = Capability(
            name=f"gov-cap-{version}",
            type="mcp",
            version=version,
            status=status,
            author_id=admin.id,
        )
        db.add(cap)
        await db.commit()
        return cap.id


class _StreamStub:
    """替换 GatewayEndpoints.handle_stream：读重放后的 body 并返回 JSON-RPC 结果。"""

    def __init__(self) -> None:
        self.bodies: list[bytes] = []
        self.delay = 0.0
        self.error = ""
        self.is_error = False
        self.error_text = "上游工具失败"

    async def handle_stream(self, scope, receive, send, config=None) -> None:
        body = b""
        while True:
            message = await receive()
            if message.get("type") != "http.request":
                break
            body += message.get("body") or b""
            if not message.get("more_body", False):
                break
        self.bodies.append(body)
        payload = json.loads(body or b"{}")
        if isinstance(payload, list):
            payload = payload[0]
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            out = {"jsonrpc": "2.0", "id": payload.get("id"), "error": {"code": -32000, "message": self.error}}
        else:
            result: dict = {"content": [{"type": "text", "text": "ok"}]}
            if self.is_error:
                result = {"content": [{"type": "text", "text": self.error_text}], "isError": True}
            out = {"jsonrpc": "2.0", "id": payload.get("id"), "result": result}
        from starlette.responses import JSONResponse

        await JSONResponse(out)(scope, receive, send)


@pytest.fixture
def stream_stub(monkeypatch):
    stub = _StreamStub()
    monkeypatch.setattr(GatewayEndpoints, "handle_stream", stub.handle_stream)
    return stub


# ---------- 身份来源（仅审计归因） ----------


@pytest.mark.asyncio
async def test_auth_require_token_rejects_without_credentials(client, monkeypatch):
    monkeypatch.setattr(get_settings(), "mcp_gateway_require_token", True)
    with pytest.raises(GatewayAuthError):
        await authenticate_request(_config(), _scope())


@pytest.mark.asyncio
async def test_auth_server_token_matches_and_wrong_token_rejected(client):
    identity = await authenticate_request(_config(token="tok"), _scope({"X-Gateway-Token": "tok"}))
    assert identity.source == "server_token"
    assert identity.user_id is None
    with pytest.raises(GatewayAuthError):
        await authenticate_request(_config(token="tok"), _scope({"X-Gateway-Token": "bad"}))


@pytest.mark.asyncio
async def test_auth_local_jwt_resolves_user(client, admin_headers, monkeypatch):
    token = admin_headers["Authorization"].split(" ", 1)[1]
    identity = await authenticate_request(_config(), _scope({"Authorization": f"Bearer {token}"}))
    assert identity.source == "jwt"
    assert identity.username == "admin"
    assert identity.role == "admin"
    assert identity.user_id
    # require_token 打开时，有效 JWT 依然能识别身份
    monkeypatch.setattr(get_settings(), "mcp_gateway_require_token", True)
    identity = await authenticate_request(_config(), _scope({"Authorization": f"Bearer {token}"}))
    assert identity.source == "jwt"


@pytest.mark.asyncio
async def test_auth_sso_token_resolves_user(client, monkeypatch):
    monkeypatch.setattr(
        "app.auth.verify_sso_token",
        lambda token, audience=None: {"sub": "sso-gw-1", "email": "sso-gw-1@example.com", "name": "网关SSO"},
    )
    identity = await authenticate_request(_config(), _scope({"Authorization": "Bearer sso-fake"}))
    assert identity.source == "sso"
    assert identity.username == "sso-gw-1"
    assert identity.user_id


@pytest.mark.asyncio
async def test_auth_service_token_resolves_user(client, admin_headers):
    r = await client.post(
        "/api/admin/service-tokens",
        headers=admin_headers,
        json={"name": "gw-svc", "scopes": ["runtime", "gateway"]},
    )
    assert r.status_code == 201, r.text
    token = r.json()["token"]

    identity = await authenticate_request(_config(), _scope({"Authorization": f"Bearer {token}"}))
    assert identity.source == "service_token"
    assert identity.username == "svc_gw-svc"
    assert identity.user_id


@pytest.mark.asyncio
async def test_auth_anonymous_allowed_when_not_required(client):
    identity = await authenticate_request(_config(), _scope())
    assert identity.source == "anonymous"
    assert identity.user_id is None


# ---------- 消息级审计 ----------


async def _post_jsonrpc(client, name, payload, headers=None):
    return await asyncio.wait_for(
        client.post(URL.format(name=name), json=payload, headers=headers or {}),
        timeout=15,
    )


@pytest.mark.asyncio
async def test_audit_records_tool_calls(client, admin_headers, stream_stub):
    cap_id = await _insert_capability()
    await _insert_server("gov-audit", capability_id=cap_id)
    headers = dict(admin_headers)

    r = await _post_jsonrpc(client, "gov-audit", {"jsonrpc": "2.0", "id": 1, "method": "initialize"}, headers)
    assert r.status_code == 200, r.text
    r = await _post_jsonrpc(
        client,
        "gov-audit",
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        headers,
    )
    assert r.status_code == 200, r.text
    r = await _post_jsonrpc(
        client,
        "gov-audit",
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "add", "arguments": {"a": 1}}},
        {**headers, "X-Conversation-Id": "conv-42"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["content"][0]["text"] == "ok"

    async with SessionLocal() as db:
        rows = (
            await db.scalars(select(MCPGatewayCall).where(MCPGatewayCall.server_name == "gov-audit"))
        ).all()
        assert [row.method for row in rows] == ["initialize", "tools_list", "tools_call"]
        row = rows[-1]
        assert row.tool == "add"
        assert row.capability_id == cap_id
        assert row.capability_version == "1.2.3"
        assert row.source == "jwt"
        assert row.username == "admin"
        assert row.user_id
        assert row.conversation_id == "conv-42"
        assert row.duration_ms >= 0
        assert row.ok is True
        assert row.error == ""


@pytest.mark.asyncio
async def test_audit_records_anonymous_calls(client, stream_stub):
    await _insert_server("gov-anon")

    r = await _post_jsonrpc(
        client,
        "gov-anon",
        {"jsonrpc": "2.0", "id": 5, "method": "tools/list"},
    )
    assert r.status_code == 200, r.text

    async with SessionLocal() as db:
        row = (
            await db.scalars(select(MCPGatewayCall).where(MCPGatewayCall.server_name == "gov-anon"))
        ).one()
        assert row.method == "tools_list"
        assert row.source == "anonymous"
        assert row.user_id == ""
        assert row.username == ""


@pytest.mark.asyncio
async def test_audit_records_upstream_error(client, stream_stub):
    await _insert_server("gov-err", api_token="err-token")
    stream_stub.is_error = True
    stream_stub.error_text = "上游炸了：超时"

    r = await _post_jsonrpc(
        client,
        "gov-err",
        {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "boom"}},
        {"X-Gateway-Token": "err-token"},
    )
    assert r.status_code == 200, r.text

    async with SessionLocal() as db:
        row = (
            await db.scalars(select(MCPGatewayCall).where(MCPGatewayCall.server_name == "gov-err"))
        ).one()
        assert row.method == "tools_call"
        assert row.tool == "boom"
        assert row.ok is False
        assert "上游炸了" in row.error
        assert row.source == "server_token"


@pytest.mark.asyncio
async def test_audit_write_failure_does_not_block_request(client, stream_stub, monkeypatch):
    await _insert_server("gov-audit-fail", api_token="audit-token")

    def _boom():
        raise RuntimeError("审计库不可用")

    monkeypatch.setattr("app.database.SessionLocal", _boom)
    r = await _post_jsonrpc(
        client,
        "gov-audit-fail",
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "add"}},
        {"X-Gateway-Token": "audit-token"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["content"][0]["text"] == "ok"


# ---------- 超时 ----------


@pytest.mark.asyncio
async def test_stream_timeout_returns_rpc_error_and_audits_failure(client, stream_stub, monkeypatch):
    await _insert_server("gov-slow", api_token="slow-token")
    monkeypatch.setattr(get_settings(), "mcp_gateway_request_timeout", 0.5)
    stream_stub.delay = 5.0

    r = await _post_jsonrpc(
        client,
        "gov-slow",
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "slow_tool"}},
        {"X-Gateway-Token": "slow-token"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == 7
    assert "超时" in body["error"]["message"]

    async with SessionLocal() as db:
        row = (
            await db.scalars(select(MCPGatewayCall).where(MCPGatewayCall.server_name == "gov-slow"))
        ).one()
        assert row.method == "tools_call"
        assert row.tool == "slow_tool"
        assert row.ok is False
        assert "超时" in row.error


def test_parse_mcp_calls_maps_methods_and_tool():
    calls = parse_mcp_calls(
        json.dumps(
            [
                {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
                {"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "add"}},
            ]
        ).encode("utf-8")
    )
    assert [(c.method, c.tool) for c in calls] == [("initialize", ""), ("other", ""), ("tools_call", "add")]
    assert parse_mcp_calls(b"not-json") == []


# ---------- 路由判定：能力级 /relay（主入口）与 /cap 别名 / 服务级裸路径 ----------


@pytest.mark.asyncio
async def test_relay_route_is_capability_requires_bearer(client, stream_stub):
    """/relay/{name}/stream 走能力级：未登录 401；登记服务令牌不是能力级凭据。"""
    await _insert_server("route-shadow", api_token="shadow-token")
    payload = {"jsonrpc": "2.0", "id": 1, "method": "initialize"}

    r = await asyncio.wait_for(
        client.post("/api/mcp-gateway/relay/route-shadow/stream", json=payload), timeout=15
    )
    assert r.status_code == 401, r.text
    assert "SSO" in r.json()["detail"]

    # 服务级登记令牌在能力级路径上不生效（若按服务级路由会 200）
    r = await asyncio.wait_for(
        client.post(
            "/api/mcp-gateway/relay/route-shadow/stream",
            json=payload,
            headers={"X-Gateway-Token": "shadow-token"},
        ),
        timeout=15,
    )
    assert r.status_code == 401, r.text
    assert "SSO" in r.json()["detail"]


@pytest.mark.asyncio
async def test_relay_route_fake_bearer_enters_capability_parsing(client, stream_stub):
    """带假 Bearer 时进入能力解析（市场令牌校验），而不是服务级登记令牌匹配。"""
    await _insert_server("route-fake")
    r = await asyncio.wait_for(
        client.post(
            "/api/mcp-gateway/relay/route-fake/stream",
            json={"jsonrpc": "2.0", "id": 2, "method": "initialize"},
            headers={"Authorization": "Bearer fake-market-token"},
        ),
        timeout=15,
    )
    assert r.status_code == 401, r.text
    detail = r.json()["detail"]
    assert "网关令牌无效" not in detail
    assert "登录" in detail


@pytest.mark.asyncio
async def test_bare_route_is_service_level(client, stream_stub):
    """服务级回到裸 /{name}/{kind}：登记令牌直接生效，无令牌仍 401。"""
    await _insert_server("bare-svc", api_token="bare-token")
    payload = {"jsonrpc": "2.0", "id": 3, "method": "tools/list"}

    r = await _post_jsonrpc(client, "bare-svc", payload, {"X-Gateway-Token": "bare-token"})
    assert r.status_code == 200, r.text
    assert r.json()["result"]["content"][0]["text"] == "ok"

    r = await _post_jsonrpc(client, "bare-svc", payload)
    assert r.status_code == 401, r.text
    assert "网关令牌无效" in r.json()["detail"]


@pytest.mark.asyncio
async def test_cap_alias_equivalent_to_relay_capability_route(
    client, publisher_headers, admin_headers, stream_stub
):
    """/cap/{name} 别名与 /relay/{name} 主入口等价：同一能力解析、同样放行。"""
    from test_mcp_debug import _mcp_zip
    from test_workflow import _publish_capability

    name = "alias-cap"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "mcp", _mcp_zip(name)
    )
    payload = {"jsonrpc": "2.0", "id": 4, "method": "tools/list"}

    relay = await asyncio.wait_for(
        client.post(f"/api/mcp-gateway/relay/{name}/stream", json=payload, headers=admin_headers),
        timeout=15,
    )
    alias = await asyncio.wait_for(
        client.post(f"/api/mcp-gateway/cap/{name}/stream", json=payload, headers=admin_headers),
        timeout=15,
    )
    assert relay.status_code == alias.status_code == 200, relay.text
    assert relay.json() == alias.json()


@pytest.mark.asyncio
async def test_reserved_namespace_two_segment_paths_rejected(client):
    """2 段裸路径中 relay/cap 为保留字，避免与命名空间歧义；其余形状仍通用 404。"""
    for reserved in ("relay", "cap"):
        r = await client.get(f"/api/mcp-gateway/{reserved}/stream")
        assert r.status_code == 404, reserved
        assert "保留字" in r.json()["detail"]

    r = await client.get("/api/mcp-gateway/relay")
    assert r.status_code == 404


def test_sse_message_endpoint_follows_route_namespace():
    """SSE 回传端点跟随访问命名空间：/relay/{name} 与 /cap/{name} 能力级、裸路径服务级。"""

    async def loader(_name):
        return None

    relay_ep = GatewayEndpoints("relay/demo", loader)
    alias_ep = GatewayEndpoints("cap/demo", loader)
    service_ep = GatewayEndpoints("demo", loader)
    assert relay_ep.sse._endpoint == "/relay/demo/messages/"
    assert alias_ep.sse._endpoint == "/cap/demo/messages/"
    assert service_ep.sse._endpoint == "/demo/messages/"
