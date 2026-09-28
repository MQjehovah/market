"""服务令牌 scope 语义 + 运行时/检索/网关一律按 Bearer 用户鉴权。

X-Act-As-Sub 代授权已删除：本文件锁定移除后的语义——
运行时端点只认用户令牌（服务令牌 403、无 token 401），
sync/relay//my 一律按 token 用户本人判定，携带该头不再切换身份。
"""

import logging
import types
import uuid

import pytest
from fastapi import HTTPException

from app.database import SessionLocal
from app.models import ServiceToken
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
            "description": "鉴权测试",
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


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


async def _sync(client, headers: dict[str, str] | None = None) -> list[dict]:
    r = await client.get("/api/capabilities/sync", headers=headers or {})
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
    r = await client.get("/api/capabilities/sync", headers=_bearer(token_bad))
    assert r.status_code == 403
    assert "sync" in r.json()["detail"]

    # 带 X-Act-As-Sub 也不放行：代授权已删除，scope 校验仍然先行
    r = await client.get(
        "/api/capabilities/sync",
        headers={**_bearer(token_bad), "X-Act-As-Sub": "admin"},
    )
    assert r.status_code == 403
    assert "sync" in r.json()["detail"]

    _, token_ok = await _make_token(client, admin_headers, ["gateway", "sync"])
    r = await client.get("/api/capabilities/sync", headers=_bearer(token_ok))
    assert r.status_code == 200

    legacy, token_legacy = await _make_token(client, admin_headers, ["sync"])
    await _clear_scopes(legacy["id"])
    with caplog.at_level(logging.WARNING, logger="market.service_tokens"):
        r = await client.get("/api/capabilities/sync", headers=_bearer(token_legacy))
    assert r.status_code == 200
    assert any("scopes" in rec.message for rec in caplog.records)


# ---------- sync：一律按 Bearer 用户可见性 ----------


@pytest.mark.asyncio
async def test_sync_visibility_follows_bearer_user(client, publisher_headers, admin_headers):
    """sync 可见性取 token 用户；订阅不再是过滤条件；X-Act-As-Sub 头完全忽略。"""
    await _create_user(client, admin_headers, "act-a", department="市场部")
    a_headers = await _login(client, "act-a")

    cap_own = await _publish(client, publisher_headers, admin_headers, "act-own")
    await _publish(client, publisher_headers, admin_headers, "act-other")
    await _publish(
        client, publisher_headers, admin_headers, "act-local-cap", distribution="local"
    )
    await _subscribe(client, a_headers, cap_own)

    # 用户可见的已发布能力全部返回（未订阅的 act-other / local 也在）
    names = {i["name"] for i in await _sync(client, a_headers)}
    assert {"act-own", "act-other", "act-local-cap"} <= names

    # 带 X-Act-As-Sub：忽略该头，结果与不带一致
    names_ghost = {i["name"] for i in await _sync(client, {**a_headers, "X-Act-As-Sub": "ghost"})}
    assert names_ghost == names

    # 服务令牌：按绑定用户自身视角；带目标用户名也不切换身份
    _, token = await _make_token(client, admin_headers, ["gateway", "sync"])
    svc_names = {i["name"] for i in await _sync(client, _bearer(token))}
    svc_names_act = {
        i["name"]
        for i in await _sync(client, {**_bearer(token), "X-Act-As-Sub": "act-a"})
    }
    assert svc_names_act == svc_names


@pytest.mark.asyncio
async def test_sync_ignores_unknown_act_as_header(client, admin_headers):
    """未知 act-as 目标不再 403：头被忽略，按 token 身份返回可见能力。"""
    _, token = await _make_token(client, admin_headers, ["sync"])
    r = await client.get(
        "/api/capabilities/sync",
        headers={**_bearer(token), "X-Act-As-Sub": "ghost"},
    )
    assert r.status_code == 200, r.text


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
        _scope(_bearer(token_runtime)), name
    )
    assert cfg is None and status == 403
    assert "gateway" in body["detail"]

    # scope 不足 + 带 X-Act-As-Sub：scope 校验先行，仍 403（不切换身份）
    cfg, status, body = await authorize_capability_gateway(
        _scope({**_bearer(token_runtime), "X-Act-As-Sub": "admin"}), name
    )
    assert cfg is None and status == 403
    assert "gateway" in body["detail"]

    # scope 充足 + 绑定管理员：runtime 门禁正常放行
    _, token_admin = await _make_token(
        client, admin_headers, ["gateway", "sync"], user_id=me["id"]
    )
    cfg, status, _ = await authorize_capability_gateway(_scope(_bearer(token_admin)), name)
    assert status is None and cfg is not None

    # 存量令牌（scopes 清空）：放行 + 告警
    meta, token_legacy = await _make_token(
        client, admin_headers, ["gateway"], user_id=me["id"]
    )
    await _clear_scopes(meta["id"])
    with caplog.at_level(logging.WARNING, logger="market.service_tokens"):
        cfg, status, _ = await authorize_capability_gateway(
            _scope(_bearer(token_legacy)), name
        )
    assert status is None and cfg is not None
    assert any("scopes" in rec.message for rec in caplog.records)

    # scope 充足但绑定普通用户未订阅：runtime 门禁照常 403（scope 不改变权限模型）
    bound = await _create_user(client, admin_headers, "svc-bound-user")
    _, token_bound = await _make_token(
        client, admin_headers, ["gateway", "sync"], user_id=bound["id"]
    )
    cfg, status, body = await authorize_capability_gateway(
        _scope(_bearer(token_bound)), name
    )
    assert cfg is None and status == 403
    assert "我的能力" in body["detail"]


# ---------- relay：用户 token 直连按本人准入 ----------


@pytest.mark.asyncio
async def test_relay_user_token_direct_access(client, publisher_headers, admin_headers):
    name = "act-relay-user"
    cap_id = await _publish(client, publisher_headers, admin_headers, name, type_="mcp")
    user = await _create_user(client, admin_headers, "act-b")
    b_headers = await _login(client, "act-b")

    # 未订阅 → runtime 门禁 403
    cfg, status, _ = await authorize_capability_gateway(_scope(b_headers), name)
    assert cfg is None and status == 403

    # 订阅后放行；审计按本人，不再有 act_as/actor 字段
    await _subscribe(client, b_headers, cap_id)
    cfg, status, _ = await authorize_capability_gateway(_scope(b_headers), name)
    assert status is None and cfg is not None
    assert cfg["_audit"]["user_id"] == user["id"]
    assert "act_as" not in cfg["_audit"]
    assert "actor_user_id" not in cfg["_audit"]

    # 携带 X-Act-As-Sub 头被忽略：仍按本人放行
    cfg, status, _ = await authorize_capability_gateway(
        _scope({**b_headers, "X-Act-As-Sub": "ghost"}), name
    )
    assert status is None and cfg is not None


@pytest.mark.asyncio
async def test_relay_rate_limits_by_bearer_user(client, publisher_headers, admin_headers):
    """限流按 token 用户单键；X-Act-As-Sub 不参与限流键，不能绕过。"""
    from app.config import get_settings
    from app.services.gateway_governance import reset_governance_for_tests

    name = "act-relay-rl"
    cap_id = await _publish(client, publisher_headers, admin_headers, name, type_="mcp")
    await _create_user(client, admin_headers, "act-rl")
    rl_headers = await _login(client, "act-rl")
    await _subscribe(client, rl_headers, cap_id)

    settings = get_settings()
    old_limit = settings.mcp_gateway_rate_limit_per_minute
    reset_governance_for_tests()
    settings.mcp_gateway_rate_limit_per_minute = 2
    try:
        cfg, status, _ = await authorize_capability_gateway(_scope(rl_headers), name)
        assert status is None and cfg is not None
        cfg, status, _ = await authorize_capability_gateway(_scope(rl_headers), name)
        assert status is None and cfg is not None
        cfg, status, body = await authorize_capability_gateway(_scope(rl_headers), name)
        assert cfg is None and status == 429
        assert "限流" in body["detail"]

        # 带 X-Act-As-Sub 不能另开限流键：同一用户键已超限，仍 429
        cfg, status, _ = await authorize_capability_gateway(
            _scope({**rl_headers, "X-Act-As-Sub": "ghost"}), name
        )
        assert status == 429
    finally:
        settings.mcp_gateway_rate_limit_per_minute = old_limit
        reset_governance_for_tests()


@pytest.mark.asyncio
async def test_relay_gate_follows_name_across_republish(
    client, publisher_headers, admin_headers
):
    """订阅旧版本后重发布：用户 token sync 返回新版本，relay 按名放行。"""
    name = "act-ver-mcp"
    cap_id = await _publish(client, publisher_headers, admin_headers, name, type_="mcp")
    await _create_user(client, admin_headers, "act-ver-u")
    u_headers = await _login(client, "act-ver-u")
    await _subscribe(client, u_headers, cap_id)

    # 重发布 1.0.1（新行）
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/versions",
        headers=publisher_headers,
        json={"new_version": "1.0.1", "changelog": "patch"},
    )
    assert r.status_code == 201, r.text
    v2 = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{v2}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", _mcp_zip(name), "application/zip")},
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/publish/capabilities/{v2}/submit", headers=publisher_headers)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/admin/capabilities/{v2}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text

    # 用户 token sync：订阅旧版本，仍返回同名最新版本
    items = {i["name"]: i for i in await _sync(client, u_headers)}
    assert items[name]["version"] == "1.0.1"

    # relay：按名判定订阅，新版本门禁放行
    cfg, status, body = await authorize_capability_gateway(_scope(u_headers), name)
    assert status is None and cfg is not None, body


# ---------- /my：一律按 Bearer 用户执行 ----------


@pytest.mark.asyncio
async def test_my_operations_follow_bearer_user(client, publisher_headers, admin_headers):
    """用户/服务令牌各按本人身份；X-Act-As-Sub 不再切换操作主体。"""
    name = "act-my-cap"
    cap_id = await _publish(client, publisher_headers, admin_headers, name, type_="tool")
    await _create_user(client, admin_headers, "act-my-user")
    a_headers = await _login(client, "act-my-user")
    _, token = await _make_token(client, admin_headers, ["gateway", "sync"])

    # 用户 token 带 X-Act-As-Sub：忽略，按本人（初始未加入）
    r = await client.get(
        "/api/my/capabilities?scope=added",
        headers={**a_headers, "X-Act-As-Sub": "ghost"},
    )
    assert r.status_code == 200, r.text
    assert not any(c["id"] == cap_id for c in r.json())

    # 服务令牌带 X-Act-As-Sub：加入落到绑定服务用户，目标用户不受影响
    svc_act = {**_bearer(token), "X-Act-As-Sub": "act-my-user"}
    r = await client.post(
        "/api/my/capabilities", headers=svc_act, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text
    r = await client.get("/api/my/capabilities?scope=added", headers=a_headers)
    assert not any(c["id"] == cap_id for c in r.json())
    r = await client.get("/api/my/capabilities?scope=added", headers=_bearer(token))
    assert any(c["id"] == cap_id for c in r.json())

    # 订阅清单同样按 token 身份，act-as 头不切换
    r = await client.post(
        "/api/subscriptions", headers=a_headers, json={"capability_name": name}
    )
    assert r.status_code == 201, r.text
    r = await client.get("/api/my/subscriptions", headers={**a_headers, "X-Act-As-Sub": "ghost"})
    assert r.json()["names"] == [name]
    r = await client.get("/api/my/subscriptions", headers=svc_act)
    assert r.json()["names"] == []


# ---------- /runtime：用户 token 直连按本人准入 ----------


@pytest.mark.asyncio
async def test_runtime_bearer_user_access(client, publisher_headers, admin_headers):
    """runtime 端点只认用户令牌：无 token 401、无准入 403、准入 200、服务令牌 403。"""
    name = "rt-user-tool"
    cap_id = await _publish(client, publisher_headers, admin_headers, name, type_="tool")
    await _create_user(client, admin_headers, "rt-u")
    u_headers = await _login(client, "rt-u")
    url = f"/api/runtime/tools/{name}/invoke"
    body = {"params": {}}

    # 无 token → 401
    r = await client.post(url, json=body)
    assert r.status_code == 401

    # 有 token 但未加入「我的能力」→ 403
    r = await client.post(url, headers=u_headers, json=body)
    assert r.status_code == 403, r.text

    # 加入后 → 200（准入按本人）
    await _subscribe(client, u_headers, cap_id)
    r = await client.post(url, headers=u_headers, json=body)
    assert r.status_code == 200, r.text

    # X-Act-As-Sub 头被忽略：不影响本人准入
    r = await client.post(url, headers={**u_headers, "X-Act-As-Sub": "ghost"}, json=body)
    assert r.status_code == 200, r.text

    # 服务令牌（即使绑定管理员）不可访问 runtime → 403
    admin_id = (await client.get("/api/auth/me", headers=admin_headers)).json()["id"]
    _, token = await _make_token(
        client, admin_headers, ["runtime", "gateway", "sync"], user_id=admin_id
    )
    r = await client.post(url, headers=_bearer(token), json=body)
    assert r.status_code == 403, r.text
    assert "服务令牌" in r.json()["detail"]
    r = await client.get("/api/runtime/mcp/discover", headers=_bearer(token))
    assert r.status_code == 403, r.text

    # 用户 token 下 discover 正常
    r = await client.get("/api/runtime/mcp/discover", headers=u_headers)
    assert r.status_code == 200, r.text
