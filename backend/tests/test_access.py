"""能力访问权限：接口行为（open / admin_only / restricted 白名单）与统一访问谓词单测。"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import _auto_migrate
from app.models import Capability, User
from app.services.access import access_deny_reason, capability_access_ok
from test_workflow import _publish_capability, _tool_zip


async def _set_access(
    client, headers, cap_id, policy, allowed=None, departments=None, roles=None
):
    """三态更新：参数为 None 表示不传（新字段保持原值），[] 表示显式清空。"""
    payload: dict = {"access_policy": policy}
    if allowed is not None:
        payload["allowed_users"] = allowed
    if departments is not None:
        payload["allowed_departments"] = departments
    if roles is not None:
        payload["allowed_roles"] = roles
    r = await client.post(
        f"/api/capabilities/{cap_id}/access",
        headers=headers,
        json=payload,
    )
    assert r.status_code == 200, r.text
    return r.json()


def _u(username: str, *, role: str = "user", department: str = "") -> User:
    return User(
        id=f"id-{username}",
        username=username,
        email=f"{username}@example.com",
        password_hash="x",
        role=role,
        department=department,
    )


def _c(**kwargs) -> Capability:
    data = {
        "id": "cap-1",
        "name": "cap",
        "type": "tool",
        "version": "1.0.0",
        "author_id": "id-author",
    }
    data.update(kwargs)
    return Capability(**data)


def test_admin_and_author_always_pass():
    author = _u("author")
    cap = _c(author_id=author.id, access_policy="admin_only", allowed_users=["someone"])
    assert capability_access_ok(cap, _u("admin", role="admin"))
    assert capability_access_ok(cap, author)


def test_anonymous_denied():
    cap = _c(access_policy="open")
    assert not capability_access_ok(cap, None)
    assert access_deny_reason(cap, None) == "请先登录"


def test_admin_only_denies_normal_user():
    cap = _c(access_policy="admin_only", allowed_departments=["研发部"])
    user = _u("u", department="研发部")
    assert not capability_access_ok(cap, user)
    assert access_deny_reason(cap, user) == "该能力仅限管理员"


def test_department_gate():
    cap = _c(access_policy="restricted", allowed_departments=["研发部"])
    assert capability_access_ok(cap, _u("u", department="研发部"))
    assert not capability_access_ok(cap, _u("u", department="市场部"))
    assert not capability_access_ok(cap, _u("u", department=""))
    assert access_deny_reason(cap, _u("u", department="市场部")) == "该能力仅限部门：研发部"


def test_role_gate():
    cap = _c(access_policy="restricted", allowed_roles=["publisher"])
    assert capability_access_ok(cap, _u("u", role="publisher"))
    assert not capability_access_ok(cap, _u("u", role="user"))
    assert access_deny_reason(cap, _u("u", role="user")) == "该能力仅限角色：publisher"


def test_whitelist_gate():
    cap = _c(access_policy="restricted", allowed_users=["alice"])
    assert capability_access_ok(cap, _u("alice"))
    assert not capability_access_ok(cap, _u("bob"))
    assert access_deny_reason(cap, _u("bob")) == "该能力仅限白名单用户"


def test_three_gates_use_and_semantics():
    cap = _c(
        access_policy="restricted",
        allowed_departments=["研发部"],
        allowed_roles=["publisher"],
        allowed_users=["alice"],
    )
    assert capability_access_ok(cap, _u("alice", role="publisher", department="研发部"))
    assert not capability_access_ok(cap, _u("alice", role="publisher", department="市场部"))
    assert not capability_access_ok(cap, _u("alice", role="user", department="研发部"))
    assert not capability_access_ok(cap, _u("bob", role="publisher", department="研发部"))


def test_blank_allowlists():
    # restricted 且三门全空：拒绝；open 全空（含缺省）：放行
    assert not capability_access_ok(_c(access_policy="restricted"), _u("u"))
    assert access_deny_reason(_c(access_policy="restricted"), _u("u")) == "没有该能力的访问权限"
    assert capability_access_ok(_c(access_policy="open"), _u("u"))
    assert capability_access_ok(_c(access_policy=None), _u("u"))
    # 空白项被忽略，等价于全空
    assert not capability_access_ok(
        _c(access_policy="restricted", allowed_departments=["", "  "]), _u("u")
    )


def test_allowlist_values_are_stripped_on_both_sides():
    assert capability_access_ok(
        _c(access_policy="restricted", allowed_departments=[" 研发部 "]),
        _u("u", department="研发部"),
    )
    assert capability_access_ok(
        _c(access_policy="restricted", allowed_departments=["研发部"]),
        _u("u", department=" 研发部 "),
    )


def test_allowlists_gate_beyond_restricted_policy():
    # 新语义：非空名单即生效（admin_only 除外），不再以 access_policy=restricted 为前提
    cap = _c(access_policy="open", allowed_roles=["publisher"])
    assert capability_access_ok(cap, _u("u", role="publisher"))
    assert not capability_access_ok(cap, _u("u", role="user"))


def test_unknown_policy_with_empty_lists_denies():
    # 未知 policy + 空名单：fail-closed
    assert not capability_access_ok(_c(access_policy="weird"), _u("u"))
    assert access_deny_reason(_c(access_policy="weird"), _u("u")) == "没有该能力的访问权限"


def test_blank_and_null_entries_ignored():
    # None/空白项跳过，不污染白名单
    cap = _c(
        access_policy="restricted",
        allowed_users=[None, "alice", None, ""],
        allowed_roles=[None, "  "],
    )
    assert capability_access_ok(cap, _u("alice"))
    assert not capability_access_ok(cap, _u("bob"))


def test_non_sequence_allowlist_treated_as_empty():
    # 脏数据（非 list/tuple）视为空名单：restricted 拒绝、open 放行
    assert not capability_access_ok(
        _c(access_policy="restricted", allowed_users="alice"), _u("alice")
    )
    assert capability_access_ok(_c(access_policy="open", allowed_roles="publisher"), _u("u"))


@pytest.mark.asyncio
async def test_admin_only_capability(client, publisher_headers, admin_headers, user_headers):
    name = "secret-tool"
    await _publish_capability(client, publisher_headers, admin_headers, name, "tool", _tool_zip(name))
    r = await client.get("/api/capabilities", params={"q": name})
    cap_id = r.json()["items"][0]["id"]
    await _set_access(client, admin_headers, cap_id, "admin_only")

    # 普通用户连订阅都会被拒（订阅门禁）
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 403
    assert "仅限管理员" in r.json()["detail"]

    # 调用同样被拒
    r = await client.post(
        f"/api/runtime/tools/{name}/invoke",
        headers=user_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 403
    assert "仅限管理员" in r.json()["detail"]

    # 作者、管理员可以调用
    r = await client.post(
        f"/api/runtime/tools/{name}/invoke",
        headers=publisher_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/runtime/tools/{name}/invoke",
        headers=admin_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_restricted_whitelist_capability(client, publisher_headers, admin_headers, user_headers):
    name = "whitelist-tool"
    # 由管理员创建（作者=admin），发布者是非作者、非白名单用户
    await _publish_capability(client, admin_headers, admin_headers, name, "tool", _tool_zip(name))
    r = await client.get("/api/capabilities", params={"q": name})
    cap_id = r.json()["items"][0]["id"]
    await _set_access(client, admin_headers, cap_id, "restricted", ["user"])

    # 白名单用户加入后可以调用
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text
    r = await client.post(
        f"/api/runtime/tools/{name}/invoke",
        headers=user_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 200, r.text

    # 非白名单、非作者的用户：订阅即被拒，调用也无权限
    r = await client.post(
        "/api/my/capabilities", headers=publisher_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 403
    assert "白名单" in r.json()["detail"]
    r = await client.post(
        f"/api/runtime/tools/{name}/invoke",
        headers=publisher_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 403
    assert "权限" in r.json()["detail"]


@pytest.mark.asyncio
async def test_access_policy_owner_only(client, publisher_headers, admin_headers, user_headers):
    name = "owner-access-tool"
    await _publish_capability(client, publisher_headers, admin_headers, name, "tool", _tool_zip(name))
    r = await client.get("/api/capabilities", params={"q": name})
    cap_id = r.json()["items"][0]["id"]

    # 作者可以改自己的已发布能力
    await _set_access(client, publisher_headers, cap_id, "admin_only")
    # 其他普通用户不能改
    r = await client.post(
        f"/api/capabilities/{cap_id}/access",
        headers=user_headers,
        json={"access_policy": "open"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_access_policy_roundtrip_departments_roles(client, publisher_headers, admin_headers):
    name = "dept-role-tool"
    await _publish_capability(client, publisher_headers, admin_headers, name, "tool", _tool_zip(name))
    r = await client.get("/api/capabilities", params={"q": name})
    cap_id = r.json()["items"][0]["id"]

    # 不传新字段：默认空列表
    body = await _set_access(client, admin_headers, cap_id, "open")
    assert body["allowed_users"] == []
    assert body["allowed_departments"] == []
    assert body["allowed_roles"] == []

    # 写入：两端 strip、丢弃空白项
    body = await _set_access(
        client,
        admin_headers,
        cap_id,
        "restricted",
        allowed=[" user "],
        departments=[" 研发部 ", " ", "市场部"],
        roles=[" publisher "],
    )
    assert body["allowed_users"] == ["user"]
    assert body["allowed_departments"] == ["研发部", "市场部"]
    assert body["allowed_roles"] == ["publisher"]

    # 详情接口同样输出
    r = await client.get(f"/api/capabilities/{cap_id}")
    assert r.status_code == 200
    detail = r.json()
    assert detail["allowed_departments"] == ["研发部", "市场部"]
    assert detail["allowed_roles"] == ["publisher"]


@pytest.mark.asyncio
async def test_access_policy_partial_update_keeps_new_allowlists(
    client, publisher_headers, admin_headers
):
    """旧调用只传 access_policy/allowed_users：新字段保持原值；显式 [] 才清空。"""
    name = "partial-access-tool"
    await _publish_capability(client, publisher_headers, admin_headers, name, "tool", _tool_zip(name))
    r = await client.get("/api/capabilities", params={"q": name})
    cap_id = r.json()["items"][0]["id"]

    await _set_access(
        client,
        admin_headers,
        cap_id,
        "restricted",
        allowed=["user"],
        departments=["研发部"],
        roles=["publisher"],
    )

    # 未传新字段：保持原值（allowed_users 按旧语义未传即清空）
    body = await _set_access(client, admin_headers, cap_id, "open")
    assert body["allowed_users"] == []
    assert body["allowed_departments"] == ["研发部"]
    assert body["allowed_roles"] == ["publisher"]

    # 显式传 []：清空
    body = await _set_access(client, admin_headers, cap_id, "restricted", departments=[], roles=[])
    assert body["allowed_departments"] == []
    assert body["allowed_roles"] == []


@pytest.mark.asyncio
async def test_subscription_author_and_admin_early_pass(
    client, publisher_headers, admin_headers
):
    """订阅链 author/admin 早退：三门全不匹配也能订阅。"""
    name = "sub-early-tool"
    await _publish_capability(client, publisher_headers, admin_headers, name, "tool", _tool_zip(name))
    r = await client.get("/api/capabilities", params={"q": name})
    cap_id = r.json()["items"][0]["id"]
    await _set_access(
        client,
        admin_headers,
        cap_id,
        "restricted",
        allowed=["stranger"],
        departments=["研发部"],
        roles=["user"],
    )

    # 作者（无部门、非白名单、角色 publisher）仍可订阅自己的能力
    r = await client.post(
        "/api/my/capabilities", headers=publisher_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text

    # 管理员同样早退
    r = await client.post(
        "/api/my/capabilities", headers=admin_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_auto_migrate_adds_access_allowlist_columns(tmp_path):
    """老库 capabilities 缺 allowed_departments/allowed_roles：迁移补列且重复执行幂等。"""
    engine = create_async_engine(
        f"sqlite+aiosqlite:///{(tmp_path / 'legacy-access.db').as_posix()}"
    )
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text("CREATE TABLE capabilities (id VARCHAR(36) NOT NULL PRIMARY KEY)")
            )
            await _auto_migrate(conn)
            result = await conn.execute(text("PRAGMA table_info(capabilities)"))
            cols = {row[1] for row in result.fetchall()}
            assert {"allowed_users", "allowed_departments", "allowed_roles"} <= cols

            # 幂等：重复迁移不报错
            await _auto_migrate(conn)
            result = await conn.execute(text("PRAGMA table_info(capabilities)"))
            assert {"allowed_departments", "allowed_roles"} <= {row[1] for row in result.fetchall()}
    finally:
        await engine.dispose()
