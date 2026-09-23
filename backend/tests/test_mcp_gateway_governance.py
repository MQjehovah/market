"""MCP 网关治理测试：鉴权贯通、能力级审计、限流与超时。

说明：本机 mcp 2.x 的 StreamableHTTPSessionManager 与网关 _ServerProxy 不兼容
（基线既有失败），治理逻辑不依赖上游 SDK，因此用 stub 替换 handle_stream，
覆盖 ASGI 全链路的鉴权/限流/审计/超时。
"""

import asyncio
import json

import pytest
from sqlalchemy import select
from starlette.responses import JSONResponse

from app.config import get_settings
from app.database import SessionLocal
from app.models import Capability, MCPGatewayCall, MCPGatewayServer, UsageEvent, User
from app.services.mcp_gateway import (
    GatewayAuthError,
    GatewayEndpoints,
    GatewayIdentity,
    authenticate_request,
    check_rate_limit,
    parse_mcp_calls,
    rate_limit_key,
    reset_rate_limits,
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

    async def handle_stream(self, scope, receive, send) -> None:
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
        await JSONResponse(out)(scope, receive, send)


@pytest.fixture
def stream_stub(monkeypatch):
    stub = _StreamStub()
    monkeypatch.setattr(GatewayEndpoints, "handle_stream", stub.handle_stream)
    return stub


@pytest.fixture(autouse=True)
def _clean_rate_limits():
    reset_rate_limits()
    yield
    reset_rate_limits()


# ---------- 鉴权 ----------


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
    # 兼容原行为：配置了 api_token 时错误令牌仍 401
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
    # require_token 打开时，有效 JWT 依然放行
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
async def test_auth_anonymous_allowed_when_not_required(client):
    identity = await authenticate_request(_config(), _scope())
    assert identity.source == "anonymous"
    assert identity.user_id is None


def test_rate_limit_key_priority():
    user = GatewayIdentity(source="jwt", user_id="u-1", username="u")
    assert rate_limit_key(user, _config(token="tok"), _scope()) == "user:u-1"
    token_key = rate_limit_key(
        GatewayIdentity(source="server_token"), _config(token="super-secret-token"), _scope()
    )
    assert token_key.startswith("token:")
    assert "super-secret-token" not in token_key
    ip_key = rate_limit_key(GatewayIdentity(source="anonymous"), _config(), _scope(client=("10.0.0.8", 1)))
    assert ip_key == "ip:10.0.0.8"


# ---------- 限流 ----------


def test_check_rate_limit_sliding_window_and_disable():
    reset_rate_limits()
    assert check_rate_limit("k", 2, now=0.0) == (True, 0)
    assert check_rate_limit("k", 2, now=1.0) == (True, 0)
    allowed, retry_after = check_rate_limit("k", 2, now=2.0)
    assert allowed is False
    assert retry_after == 58  # 60s - (2.0 - 0.0)
    assert check_rate_limit("k", 2, now=61.0) == (True, 0)
    assert check_rate_limit("k2", 2, now=2.0) == (True, 0)  # 不同身份键互不影响
    assert check_rate_limit("off", 0, now=0.0) == (True, 0)
    assert check_rate_limit("off", 0, now=0.1) == (True, 0)


@pytest.mark.asyncio
async def test_http_rate_limit_returns_429_with_retry_after(client, monkeypatch):
    await _insert_server("gov-rate")
    monkeypatch.setattr(get_settings(), "mcp_gateway_rate_limit_per_min", 2)
    url = "/api/mcp-gateway/gov-rate/nope"
    assert (await client.post(url)).status_code == 404
    assert (await client.post(url)).status_code == 404
    r = await client.post(url)
    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1


@pytest.mark.asyncio
async def test_http_rate_limit_zero_disables(client, monkeypatch):
    await _insert_server("gov-rate-off")
    monkeypatch.setattr(get_settings(), "mcp_gateway_rate_limit_per_min", 0)
    url = "/api/mcp-gateway/gov-rate-off/nope"
    for _ in range(5):
        assert (await client.post(url)).status_code == 404


# ---------- 审计 ----------


async def _post_jsonrpc(client, name, payload, headers=None):
    return await asyncio.wait_for(
        client.post(URL.format(name=name), json=payload, headers=headers or {}),
        timeout=15,
    )


@pytest.mark.asyncio
async def test_audit_records_tool_call_and_usage(client, admin_headers, stream_stub):
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
        usage = (await db.scalars(select(UsageEvent).where(UsageEvent.action == "mcp_call"))).all()
        assert len(usage) == 1
        assert usage[0].params["tool"] == "add"
        cap = await db.get(Capability, cap_id)
        assert cap.usage_count == 1


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
