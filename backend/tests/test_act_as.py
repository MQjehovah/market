"""P5：服务令牌 scope 强制 + X-Act-As-Sub 代表用户访问。"""

import logging
import types
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.database import SessionLocal
from app.models import ServiceToken, UserCapability
from app.services.mcp_gateway import authorize_capability_gateway
from app.services.service_tokens import is_service_token, require_service_scope
from test_mcp_debug import _mcp_zip
from test_workflow import _tool_zip


def _scope(headers: dict[str, str] | None = None) -> dict:
    """能力级 relay 的最小 ASGI scope。"""
    raw = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "GET",
        "scheme": "http",
        "path": "/api/mcp-gateway/relay/x/sse",
        "raw_path": b"/api/mcp-gateway/relay/x/sse",
        "root_path": "",
        "query_string": b"",
        "headers": raw,
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }


async def _make_token(client, admin_headers, scopes, user_id=None) -> tuple[dict, str]:
    """创建服务令牌，返回 (元数据, 明文)。"""
    payload = {
        "name": f"t-{uuid.uuid4().hex[:8]}",
        "scopes": scopes,
    }
    if user_id:
        payload["user_id"] = user_id
    r = await client.post("/api/admin/service-tokens", headers=admin_headers, json=payload)
    assert r.status_code == 201, r.text
    body = r.json()
    return body, body["token"]


async def _clear_scopes(token_id: str) -> None:
    """模拟存量令牌：直接清空 scopes（创建接口要求至少一个 scope）。"""
    async with SessionLocal() as db:
        row = await db.get(ServiceToken, token_id)
        assert row is not None
        row.scopes = []
        await db.commit()


async def _create_user(client, admin_headers, username: str, *, department: str = "") -> dict:
    r = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "secret123",
            "department": department,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _login(client, username: str) -> dict:
    r = await client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _publish(
    client,
    publisher_headers,
    admin_headers,
    name: str,
    *,
    type_: str = "tool",
    distribution: str = "remote",
) -> str:
    pkg = _mcp_zip(name) if type_ == "mcp" else _tool_zip(name)
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "description": "act-as 测试",
            "type": type_,
            "version": "1.0.0",
            "category": "测试",
            "tags": [],
            "visibility": "internal",
            "distribution": distribution,
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", pkg, "application/zip")},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    return cap_id


async def _subscribe(client, headers, cap_id: str) -> None:
    r = await client.post(
        "/api/my/capabilities", headers=headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text


async def _restrict_department(client, admin_headers, cap_id: str, department: str) -> None:
    r = await client.post(
        f"/api/capabilities/{cap_id}/access",
        headers=admin_headers,
        json={"access_policy": "restricted", "allowed_departments": [department]},
    )
    assert r.status_code == 200, r.text


async def _sync(client, token: str | None = None, act_as: str | None = None) -> list[dict]:
    headers: dict[str, str] = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if act_as is not None:
        headers["X-Act-As-Sub"] = act_as
    r = await client.get("/api/capabilities/sync", headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ---------- require_service_scope 单元语义 ----------


def test_require_service_scope_unit(caplog):
    user = types.SimpleNamespace(username="svc", _service_token_scopes=["runtime"])
    assert is_service_token(user)
    with pytest.raises(HTTPException) as exc:
        require_service_scope(user, "sync")
    assert exc.value.status_code == 403
    assert "sync" in exc.value.detail
    require_service_scope(user, "runtime")  # 命中 scope 放行

    # 非服务令牌（无属性 / 显式 None）→ 403
    with pytest.raises(HTTPException) as exc:
        require_service_scope(types.SimpleNamespace(username="alice"), "sync")
    assert exc.value.status_code == 403
    with pytest.raises(HTTPException):
        require_service_scope(
            types.SimpleNamespace(username="alice", _service_token_scopes=None), "sync"
        )

    # 存量令牌（空列表）→ 放行 + 告警（含令牌前缀与绑定用户）
    legacy = types.SimpleNamespace(
        username="svc_legacy", _service_token_scopes=[], _service_token_prefix="mkt_svc_abc"
    )
    with caplog.at_level(logging.WARNING, logger="market.service_tokens"):
        require_service_scope(legacy, "sync")
    assert any("mkt_svc_abc" in rec.message and "svc_legacy" in rec.message for rec in caplog.records)


# ---------- sync：scope 强制与存量放行 ----------


@pytest.mark.asyncio
async def test_sync_scope_enforced_and_legacy_passes(client, admin_headers, caplog):
    _, token_bad = await _make_token(client, admin_headers, ["gateway"])
    r = await client.get(
        "/api/capabilities/sync", headers={"Authorization": f"Bearer {token_bad}"}
    )
    assert r.status_code == 403
    assert "sync" in r.json()["detail"]

    # scope 不足 + 带 X-Act-As-Sub：scope 校验先行，仍 403
    r = await client.get(
        "/api/capabilities/sync",
        headers={"Authorization": f"Bearer {token_bad}", "X-Act-As-Sub": "admin"},
    )
    assert r.status_code == 403
    assert "sync" in r.json()["detail"]

    _, token_ok = await _make_token(client, admin_headers, ["gateway", "sync"])
    r = await client.get(
        "/api/capabilities/sync", headers={"Authorization": f"Bearer {token_ok}"}
    )
    assert r.status_code == 200

    legacy, token_legacy = await _make_token(client, admin_headers, ["sync"])
    await _clear_scopes(legacy["id"])
    with caplog.at_level(logging.WARNING, logger="market.service_tokens"):
        r = await client.get(
            "/api/capabilities/sync", headers={"Authorization": f"Bearer {token_legacy}"}
        )
    assert r.status_code == 200
    assert any("scopes" in rec.message for rec in caplog.records)


# ---------- sync：X-Act-As-Sub 视角过滤 ----------


@pytest.mark.asyncio
async def test_sync_act_as_filters_subscribed_visible(client, publisher_headers, admin_headers):
    await _create_user(client, admin_headers, "act-a", department="市场部")
    a_headers = await _login(client, "act-a")

    cap_in = await _publish(client, publisher_headers, admin_headers, "act-in")
    await _publish(client, publisher_headers, admin_headers, "act-out")
    cap_denied = await _publish(client, publisher_headers, admin_headers, "act-denied")
    cap_local = await _publish(
        client, publisher_headers, admin_headers, "act-local", distribution="local"
    )
    for cap_id in (cap_in, cap_denied, cap_local):
        await _subscribe(client, a_headers, cap_id)
    # 已订阅后收紧部门白名单：有订阅但无权访问
    await _restrict_department(client, admin_headers, cap_denied, "研发部")

    _, token = await _make_token(client, admin_headers, ["gateway", "sync"])

    # 不带 act-as：服务自身视角不变（含未订阅 / local）
    plain = {i["name"] for i in await _sync(client, token)}
    assert {"act-in", "act-out", "act-denied", "act-local"} <= plain

    # 带 act-as：仅 A 已订阅 + 可访问 + remote/both
    names = {i["name"] for i in await _sync(client, token, act_as="act-a")}
    assert "act-in" in names
    assert "act-out" not in names
    assert "act-denied" not in names
    assert "act-local" not in names


@pytest.mark.asyncio
async def test_sync_act_as_admin_skips_subscription(client, publisher_headers, admin_headers):
    """admin 目标用户不依赖订阅（与 access.py 早退一致），等同全量可访问集；local 仍排除。"""
    await _publish(client, publisher_headers, admin_headers, "act-admin-remote")
    await _publish(
        client, publisher_headers, admin_headers, "act-admin-local", distribution="local"
    )
    me = (await client.get("/api/auth/me", headers=admin_headers)).json()
    async with SessionLocal() as db:
        joined = (
            await db.scalars(select(UserCapability).where(UserCapability.user_id == me["id"]))
        ).all()
    assert not joined, "admin 不应依赖订阅"

    _, token = await _make_token(client, admin_headers, ["gateway", "sync"])
    names = {i["name"] for i in await _sync(client, token, act_as="admin")}
    assert "act-admin-remote" in names  # 未订阅也能拿到
    assert "act-admin-local" not in names  # distribution=local 仍排除


@pytest.mark.asyncio
async def test_sync_act_as_rejects_unknown_and_disabled(client, admin_headers):
    user = await _create_user(client, admin_headers, "act-off")
    _, token = await _make_token(client, admin_headers, ["sync"])

    r = await client.get(
        "/api/capabilities/sync",
        headers={"Authorization": f"Bearer {token}", "X-Act-As-Sub": "ghost"},
    )
    assert r.status_code == 403
    assert "act-as" in r.json()["detail"]

    r = await client.patch(
        f"/api/admin/users/{user['id']}", headers=admin_headers, json={"is_active": False}
    )
    assert r.status_code == 200
    r = await client.get(
        "/api/capabilities/sync",
        headers={"Authorization": f"Bearer {token}", "X-Act-As-Sub": "act-off"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_sync_non_service_token_ignores_act_as(client, publisher_headers, admin_headers):
    await _publish(client, publisher_headers, admin_headers, "act-jwt-cap")
    r = await client.get(
        "/api/capabilities/sync",
        headers={**admin_headers, "X-Act-As-Sub": "ghost"},
    )
    assert r.status_code == 200
    assert any(i["name"] == "act-jwt-cap" for i in r.json())


# ---------- relay：scope 门禁与绑定用户权限 ----------


@pytest.mark.asyncio
async def test_relay_scope_gate_and_bound_user(client, publisher_headers, admin_headers, caplog):
    name = "act-relay-scope"
    await _publish(client, publisher_headers, admin_headers, name, type_="mcp")
    me = (await client.get("/api/auth/me", headers=admin_headers)).json()

    # scope 不足：即使绑定管理员也被 scope 门禁拒绝
    _, token_runtime = await _make_token(
        client, admin_headers, ["runtime"], user_id=me["id"]
    )
    cfg, status, body = await authorize_capability_gateway(
        _scope({"Authorization": f"Bearer {token_runtime}"}), name
    )
    assert cfg is None and status == 403
    assert "gateway" in body["detail"]

    # scope 不足 + 带 X-Act-As-Sub：scope 校验先行，仍 403（不切换身份）
    cfg, status, body = await authorize_capability_gateway(
        _scope({"Authorization": f"Bearer {token_runtime}", "X-Act-As-Sub": "admin"}), name
    )
    assert cfg is None and status == 403
    assert "gateway" in body["detail"]

    # scope 充足 + 绑定管理员：runtime 门禁正常放行
    _, token_admin = await _make_token(
        client, admin_headers, ["gateway", "sync"], user_id=me["id"]
    )
    cfg, status, _ = await authorize_capability_gateway(
        _scope({"Authorization": f"Bearer {token_admin}"}), name
    )
    assert status is None and cfg is not None

    # 存量令牌（scopes 清空）：放行 + 告警
    meta, token_legacy = await _make_token(
        client, admin_headers, ["gateway"], user_id=me["id"]
    )
    await _clear_scopes(meta["id"])
    with caplog.at_level(logging.WARNING, logger="market.service_tokens"):
        cfg, status, _ = await authorize_capability_gateway(
            _scope({"Authorization": f"Bearer {token_legacy}"}), name
        )
    assert status is None and cfg is not None
    assert any("scopes" in rec.message for rec in caplog.records)

    # scope 充足但绑定普通用户未订阅：runtime 门禁照常 403（scope 不改变权限模型）
    bound = await _create_user(client, admin_headers, "svc-bound-user")
    _, token_bound = await _make_token(
        client, admin_headers, ["gateway", "sync"], user_id=bound["id"]
    )
    cfg, status, body = await authorize_capability_gateway(
        _scope({"Authorization": f"Bearer {token_bound}"}), name
    )
    assert cfg is None and status == 403
    assert "我的能力" in body["detail"]


# ---------- relay：X-Act-As-Sub 代表用户 ----------


@pytest.mark.asyncio
async def test_relay_act_as_uses_target_user(client, publisher_headers, admin_headers):
    name = "act-relay-as"
    cap_id = await _publish(client, publisher_headers, admin_headers, name, type_="mcp")
    user = await _create_user(client, admin_headers, "act-b")
    b_headers = await _login(client, "act-b")
    meta, token = await _make_token(client, admin_headers, ["gateway", "sync"])

    # 服务令牌自身视角：runtime 门禁拒绝
    cfg, status, _ = await authorize_capability_gateway(
        _scope({"Authorization": f"Bearer {token}"}), name
    )
    assert cfg is None and status == 403

    # act-as 用户不存在 → 403
    cfg, status, body = await authorize_capability_gateway(
        _scope({"Authorization": f"Bearer {token}", "X-Act-As-Sub": "ghost"}), name
    )
    assert cfg is None and status == 403
    assert "act-as" in body["detail"]

    # act-as 目标未订阅 → 403
    cfg, status, _ = await authorize_capability_gateway(
        _scope({"Authorization": f"Bearer {token}", "X-Act-As-Sub": "act-b"}), name
    )
    assert cfg is None and status == 403

    # act-as 目标已订阅 → 放行，审计标注 act_as
    await _subscribe(client, b_headers, cap_id)
    cfg, status, _ = await authorize_capability_gateway(
        _scope({"Authorization": f"Bearer {token}", "X-Act-As-Sub": "act-b"}), name
    )
    assert status is None and cfg is not None
    assert cfg["_audit"]["act_as"] == "act-b"
    assert cfg["_audit"]["user_id"] == user["id"]
    assert cfg["_audit"]["actor_user_id"] == meta["user_id"]
    assert cfg["_audit"]["actor_user_id"] != user["id"]


@pytest.mark.asyncio
async def test_relay_act_as_rate_limits_actor(client, publisher_headers, admin_headers):
    """act-as 时 actor 与目标用户双键限流：actor 超限即 429。"""
    from app.config import get_settings
    from app.services.gateway_governance import check_rate_limit, reset_governance_for_tests

    name = "act-relay-rl"
    cap_id = await _publish(client, publisher_headers, admin_headers, name, type_="mcp")
    await _create_user(client, admin_headers, "act-rl")
    rl_headers = await _login(client, "act-rl")
    await _subscribe(client, rl_headers, cap_id)  # 目标用户权限通畅，排除 403 干扰
    meta, token = await _make_token(client, admin_headers, ["gateway", "sync"])

    settings = get_settings()
    old_limit = settings.mcp_gateway_rate_limit_per_minute
    reset_governance_for_tests()
    settings.mcp_gateway_rate_limit_per_minute = 2
    try:
        # 预热 actor 键至超限；目标用户键保持干净
        assert check_rate_limit(f"svc:{meta['user_id']}")[0]
        assert check_rate_limit(f"svc:{meta['user_id']}")[0]
        cfg, status, body = await authorize_capability_gateway(
            _scope({"Authorization": f"Bearer {token}", "X-Act-As-Sub": "act-rl"}), name
        )
        assert cfg is None and status == 429
        assert "限流" in body["detail"]

        # 对照：同一令牌不带 act-as 走 user 键（干净）→ 非 429，actor 键不误伤普通服务调用
        cfg, status, _ = await authorize_capability_gateway(
            _scope({"Authorization": f"Bearer {token}"}), name
        )
        assert status != 429
    finally:
        settings.mcp_gateway_rate_limit_per_minute = old_limit
        reset_governance_for_tests()


@pytest.mark.asyncio
async def test_relay_non_service_token_ignores_act_as(client, publisher_headers, admin_headers):
    name = "act-relay-jwt"
    await _publish(client, publisher_headers, admin_headers, name, type_="mcp")
    cfg, status, _ = await authorize_capability_gateway(
        _scope({"Authorization": admin_headers["Authorization"], "X-Act-As-Sub": "ghost"}),
        name,
    )
    assert status is None and cfg is not None
