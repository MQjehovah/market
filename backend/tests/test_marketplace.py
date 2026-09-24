"""核心流程测试：认证 / 发布审核 / 权限矩阵 / 搜索 / 执行引擎 / 评分订阅。"""

import io
import json
import zipfile

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import joinedload

from app.auth import hash_password
from app.database import SessionLocal
from app.models import Capability, User
from app.services.marketplace import resolve_capability
from app.services.visibility import is_capability_visible, visibility_condition
from test_workflow import _publish_capability


def _tool_zip(name: str, version: str = "1.0.0") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("tool.json", json.dumps({"name": name, "description": "e2e", "version": version}))
        zf.writestr("schema.json", json.dumps({"type": "object", "properties": {}}))
        zf.writestr("implementation/tool.py", "def run(params):\n    return {}\n")
    return buf.getvalue()


@pytest.mark.asyncio
async def test_public_browse_returns_published(client):
    r = await client.get("/api/capabilities")
    assert r.status_code == 200
    items = r.json()["items"]
    # 默认浏览：助手 + 依赖（技能 / 连接器）
    assert len(items) >= 1
    assert all(item["status"] == "published" for item in items)
    types = {item["type"] for item in items}
    assert types <= {"agent", "skill", "mcp"}
    assert "agent" in types or "skill" in types or "mcp" in types

    r_all = await client.get("/api/capabilities?include_bricks=true")
    assert r_all.status_code == 200
    all_types = {item["type"] for item in r_all.json()["items"]}
    assert {"agent", "tool", "skill", "mcp"} <= all_types


@pytest.mark.asyncio
async def test_register_disabled_login_still_works(client, user_headers):
    """企业平台不再开放自助注册(由管理员或 SSO 开号), 登录仍然可用。"""
    r = await client.post(
        "/api/auth/register",
        json={"username": "newbie", "email": "newbie@example.com", "password": "secret123"},
    )
    assert r.status_code in (404, 405), r.text

    r = await client.get("/api/auth/me", headers=user_headers)
    assert r.status_code == 200
    assert r.json()["username"]



@pytest.mark.asyncio
async def test_publish_submit_review_publish_flow(client, publisher_headers, admin_headers):
    name = "测试流程工具"
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "description": "端到端流程测试",
            "type": "tool",
            "version": "1.0.0",
            "category": "测试",
            "tags": ["测试"],
            "visibility": "internal",
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    assert r.json()["status"] == "draft"

    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", _tool_zip(name), "application/zip")},
    )
    assert r.status_code == 200, r.text

    r = await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "reviewing"

    r = await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "通过"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"

    r = await client.get(f"/api/capabilities/{cap_id}")
    assert r.status_code == 200
    assert r.json()["name"] == name

    r = await client.get("/api/notifications", headers=publisher_headers)
    assert r.status_code == 200
    notes = [n for n in r.json() if n.get("link") == f"/capabilities/{cap_id}"]
    assert notes, r.text
    assert "已通过审核并发布" in notes[0]["title"]


@pytest.mark.asyncio
async def test_normal_user_can_create_own_draft(client, user_headers):
    """任何登录用户都可以创建自己的能力草稿（审核上架仍由管理员把关）。"""
    r = await client.post(
        "/api/publish/capabilities",
        headers=user_headers,
        json={"name": "越权工具", "type": "tool", "description": "x"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "draft"
    assert r.json()["author_id"] != ""


@pytest.mark.asyncio
async def test_normal_user_cannot_review(client, user_headers):
    r = await client.post(
        "/api/admin/capabilities/any-id/review",
        headers=user_headers,
        json={"action": "approve"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_search_and_filter(client):
    r = await client.get("/api/capabilities", params={"q": "数字中台"})
    assert r.status_code == 200
    assert any(item["name"] == "数字中台分析师" for item in r.json()["items"])

    r = await client.get("/api/capabilities", params={"type": "mcp"})
    assert r.status_code == 200
    assert all(item["type"] == "mcp" for item in r.json()["items"])

    r = await client.get("/api/capabilities", params={"shelf": "brick"})
    assert r.status_code == 200
    assert all(item["type"] in ("skill", "mcp", "tool") for item in r.json()["items"])
    assert any(item["type"] == "tool" for item in r.json()["items"])


@pytest.mark.asyncio
async def test_runtime_invoke_records_usage(client, admin_headers):
    r = await client.post(
        "/api/runtime/tools/%E6%96%87%E4%BB%B6%E5%93%88%E5%B8%8C%E8%AE%A1%E7%AE%97/invoke",
        headers=admin_headers,
        json={"params": {"path": "/tmp/a.txt"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["action"] == "invoke"

    r = await client.get("/api/admin/stats", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["total_usage"] >= 1


@pytest.mark.asyncio
async def test_normal_user_cannot_invoke_runtime(client, user_headers):
    """外部调用需授权：普通用户不能直接调用/执行能力。"""
    r = await client.post(
        "/api/runtime/tools/%E6%96%87%E4%BB%B6%E5%93%88%E5%B8%8C%E8%AE%A1%E7%AE%97/invoke",
        headers=user_headers,
        json={"params": {"path": "/tmp/a.txt"}},
    )
    assert r.status_code == 403
    assert "权限" in r.json()["detail"]


@pytest.mark.asyncio
async def test_mcp_discover(client, user_headers):
    r = await client.get("/api/runtime/mcp/discover", headers=user_headers)
    assert r.status_code == 200
    names = [item["name"] for item in r.json()["discovered"]]
    assert "PostgreSQL 连接器" in names


@pytest.mark.asyncio
async def test_rating_and_subscription(client, user_headers):
    r = await client.get("/api/capabilities", params={"q": "文件哈希"})
    cap_id = r.json()["items"][0]["id"]
    r = await client.post(
        f"/api/capabilities/{cap_id}/ratings",
        headers=user_headers,
        json={"score": 4, "comment": "不错"},
    )
    assert r.status_code == 201

    r = await client.post(
        "/api/subscriptions", headers=user_headers, json={"capability_name": "文件哈希计算"}
    )
    assert r.status_code == 201

    r = await client.get("/api/notifications", headers=user_headers)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_duplicate_version_conflict(client, publisher_headers):
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": "重复版本测试",
            "type": "skill",
            "description": "x",
            "version": "0.1.0",
        },
    )
    assert r.status_code == 201
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": "重复版本测试",
            "type": "skill",
            "description": "x",
            "version": "0.1.0",
        },
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_visibility_private(client, publisher_headers, user_headers):
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": "私有能力A",
            "type": "agent",
            "description": "仅作者可见",
            "visibility": "private",
        },
    )
    assert r.status_code == 201
    cap_id = r.json()["id"]
    r = await client.get(f"/api/capabilities/{cap_id}", headers=user_headers)
    assert r.status_code == 404
    r = await client.get(f"/api/capabilities/{cap_id}", headers=publisher_headers)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_team_visibility_resolvable_by_teammate(client):
    """team 可见能力：同团队可解析；非同团队与匿名均 404。"""
    async with SessionLocal() as db:
        teammate = User(
            username="mate",
            email="mate@example.com",
            password_hash=hash_password("teammate-secret-123"),
            role="user",
            team="中台团队",  # 与 publisher 同队
        )
        db.add(teammate)
        await db.commit()
        await db.refresh(teammate)
        cap = await resolve_capability(db, teammate, "代码审查专家")
        assert cap.name == "代码审查专家"

    async with SessionLocal() as db:
        outsider = await db.scalar(select(User).where(User.username == "user"))
        with pytest.raises(HTTPException) as exc:
            await resolve_capability(db, outsider, "代码审查专家")
        assert exc.value.status_code == 404

    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await resolve_capability(db, None, "代码审查专家")
        assert exc.value.status_code == 404


VISIBILITY_MATRIX = {
    "internal": {"author": True, "teammate": True, "outsider": True, "admin": True, "anonymous": True},
    "public": {"author": True, "teammate": True, "outsider": True, "admin": True, "anonymous": True},
    "private": {"author": True, "teammate": False, "outsider": False, "admin": True, "anonymous": False},
    "team": {"author": True, "teammate": True, "outsider": False, "admin": True, "anonymous": False},
}


@pytest.mark.asyncio
async def test_visibility_sql_condition_matches_python_predicate(client):
    """SQL 过滤条件与 Python 谓词在 4 种可见性 × 5 类用户上逐格等价。"""
    async with SessionLocal() as db:
        publisher = await db.scalar(select(User).where(User.username == "publisher"))
        admin = await db.scalar(select(User).where(User.username == "admin"))
        outsider = await db.scalar(select(User).where(User.username == "user"))
        teammate = User(
            username="mate",
            email="mate@example.com",
            password_hash=hash_password("teammate-secret-123"),
            role="user",
            team="中台团队",
        )
        db.add(teammate)
        names = {visibility: f"等价性-{visibility}" for visibility in VISIBILITY_MATRIX}
        for visibility, name in names.items():
            db.add(
                Capability(
                    name=name,
                    description="等价性",
                    type="tool",
                    version="1.0.0",
                    status="published",
                    visibility=visibility,
                    author_id=publisher.id,
                    organization=publisher.organization,
                )
            )
        await db.commit()
        await db.refresh(teammate)

        personas = {
            "author": publisher,
            "teammate": teammate,
            "outsider": outsider,
            "admin": admin,
            "anonymous": None,
        }
        caps = {
            c.name: c
            for c in (
                await db.scalars(select(Capability).options(joinedload(Capability.author)))
            ).all()
            if c.name in names.values()
        }
        for persona, user in personas.items():
            condition = visibility_condition(user)
            stmt = select(Capability.name)
            if condition is not None:
                stmt = stmt.where(condition)
            sql_visible = set((await db.scalars(stmt)).all())
            for visibility, name in names.items():
                expected = VISIBILITY_MATRIX[visibility][persona]
                assert is_capability_visible(caps[name], user) == expected, (
                    f"Python 谓词不符: visibility={visibility}, persona={persona}"
                )
                assert (name in sql_visible) == expected, (
                    f"SQL 条件不符: visibility={visibility}, persona={persona}"
                )


async def _publish_tool(client, publisher_headers, admin_headers, name: str) -> str:
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "tool", _tool_zip(name)
    )
    r = await client.get("/api/capabilities", params={"q": name})
    assert r.status_code == 200
    return next(c["id"] for c in r.json()["items"] if c["name"] == name)


async def _set_access(client, headers, cap_id, policy, **fields) -> dict:
    r = await client.post(
        f"/api/capabilities/{cap_id}/access",
        headers=headers,
        json={"access_policy": policy, **fields},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _set_user_department(username: str, department: str) -> None:
    async with SessionLocal() as db:
        user = await db.scalar(select(User).where(User.username == username))
        assert user is not None
        user.department = department
        await db.commit()


@pytest.mark.asyncio
async def test_subscription_denied_without_department(client, publisher_headers, admin_headers, user_headers):
    """订阅门禁：部门不匹配 403（文案含部门名）；命中后可以加入并调用。"""
    cap_id = await _publish_tool(client, publisher_headers, admin_headers, "dept-gate-tool")
    await _set_access(client, admin_headers, cap_id, "restricted", allowed_departments=["研发部"])

    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 403, r.text
    assert "加入" in r.json()["detail"]
    assert "研发部" in r.json()["detail"]

    await _set_user_department("user", "研发部")
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text
    r = await client.post(
        "/api/runtime/tools/dept-gate-tool/invoke", headers=user_headers, json={"params": {}}
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_subscription_denied_when_not_visible(client, publisher_headers, user_headers):
    """他人 private 能力订阅返回 404，不泄露存在性。"""
    async with SessionLocal() as db:
        publisher = await db.scalar(select(User).where(User.username == "publisher"))
        cap = Capability(
            name="private-join-tool",
            type="tool",
            version="1.0.0",
            status="published",
            visibility="private",
            author_id=publisher.id,
            organization=publisher.organization,
        )
        db.add(cap)
        await db.commit()
        await db.refresh(cap)
        cap_id = cap.id

    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 404
    assert r.json()["detail"] == "能力不存在"


@pytest.mark.asyncio
async def test_subscription_team_visibility_visible_to_teammate(client, user_headers):
    """team 可见能力：同团队可订阅，非同团队 404（校验不触发异步懒加载异常）。"""
    async with SessionLocal() as db:
        publisher = await db.scalar(select(User).where(User.username == "publisher"))
        cap = Capability(
            name="team-join-tool",
            type="tool",
            version="1.0.0",
            status="published",
            visibility="team",
            author_id=publisher.id,
            organization=publisher.organization,
        )
        teammate = User(
            username="mate-for-join",
            email="mate-for-join@example.com",
            password_hash=hash_password("teammate-secret-123"),
            role="user",
            team="中台团队",
        )
        db.add_all([cap, teammate])
        await db.commit()
        await db.refresh(cap)
        cap_id = cap.id

    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 404

    r = await client.post(
        "/api/auth/login", json={"username": "mate-for-join", "password": "teammate-secret-123"}
    )
    assert r.status_code == 200
    teammate_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.post(
        "/api/my/capabilities", headers=teammate_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_join_plugin_skips_components_without_access(
    client, publisher_headers, admin_headers, user_headers
):
    """plugin 含无权组件：主能力加入成功，跳过数写入返回文案。"""
    from test_plugin import _plugin_zip

    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "权限插件", "type": "plugin", "version": "1.0.0", "description": "x"},
    )
    assert r.status_code == 201, r.text
    plugin_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{plugin_id}/artifact",
        headers=publisher_headers,
        files={"file": ("p.zip", _plugin_zip(name="权限插件", version="1.0.0"), "application/zip")},
    )
    assert r.status_code == 200, r.text
    comps = r.json()["input_schema"]["components"]
    await client.post(f"/api/publish/capabilities/{plugin_id}/submit", headers=publisher_headers)
    await client.post(
        f"/api/admin/capabilities/{plugin_id}/review",
        headers=admin_headers,
        json={"action": "approve"},
    )

    blocked = next(c for c in comps if c["type"] == "skill")
    await _set_access(client, publisher_headers, blocked["capability_id"], "admin_only")

    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": plugin_id}
    )
    assert r.status_code == 201, r.text
    assert "1 个组件因权限不足未加入" in r.json()["message"]

    r = await client.get(
        "/api/my/capabilities?scope=added&include_components=true", headers=user_headers
    )
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert plugin_id in ids
    assert blocked["capability_id"] not in ids
    for c in comps:
        if c["capability_id"] != blocked["capability_id"]:
            assert c["capability_id"] in ids


@pytest.mark.asyncio
async def test_runtime_access_follows_name_across_republish(
    client, publisher_headers, admin_headers, user_headers
):
    """订阅旧版本后重发布：同名最新版本调用仍放行（订阅按能力名跨版本，之前 403）。"""
    name = "跨版本工具"
    cap_id = await _publish_capability(
        client, publisher_headers, admin_headers, name, "tool", _tool_zip(name)
    )
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text

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
        files={"file": ("pkg.zip", _tool_zip(name), "application/zip")},
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

    # 订阅者（订阅的是旧行）调用最新版本 → 放行
    r = await client.post(
        f"/api/runtime/tools/{name}/invoke",
        headers=user_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 200, r.text

    # 未订阅者仍被拒
    r = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "outsider", "email": "outsider@example.com", "password": "secret123"},
    )
    assert r.status_code == 201, r.text
    r = await client.post(
        "/api/auth/login", json={"username": "outsider", "password": "secret123"}
    )
    assert r.status_code == 200
    outsider_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.post(
        f"/api/runtime/tools/{name}/invoke",
        headers=outsider_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_runtime_denied_when_department_changed_after_join(
    client, publisher_headers, admin_headers, user_headers
):
    """已加入但部门不匹配：运行时仍 403（订阅后收紧策略即时生效）。"""
    cap_id = await _publish_tool(client, publisher_headers, admin_headers, "dept-runtime-tool")
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text

    await _set_access(client, admin_headers, cap_id, "restricted", allowed_departments=["研发部"])
    r = await client.post(
        "/api/runtime/tools/dept-runtime-tool/invoke", headers=user_headers, json={"params": {}}
    )
    assert r.status_code == 403
    assert "研发部" in r.json()["detail"]


@pytest.mark.asyncio
async def test_runtime_requires_join_even_with_matching_department(
    client, publisher_headers, admin_headers, user_headers
):
    """部门匹配但未加入：仍然 403（运行时保持须订阅）。"""
    cap_id = await _publish_tool(client, publisher_headers, admin_headers, "dept-nomember-tool")
    await _set_access(client, admin_headers, cap_id, "restricted", allowed_departments=["研发部"])
    await _set_user_department("user", "研发部")

    r = await client.post(
        "/api/runtime/tools/dept-nomember-tool/invoke", headers=user_headers, json={"params": {}}
    )
    assert r.status_code == 403
    assert "已加入" in r.json()["detail"]


@pytest.mark.asyncio
async def test_runtime_admin_only_denied_even_after_join(
    client, publisher_headers, admin_headers, user_headers
):
    """admin_only：即使先前已加入，也不可调用。"""
    cap_id = await _publish_tool(client, publisher_headers, admin_headers, "join-then-admin-tool")
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text

    await _set_access(client, admin_headers, cap_id, "admin_only")
    r = await client.post(
        "/api/runtime/tools/join-then-admin-tool/invoke", headers=user_headers, json={"params": {}}
    )
    assert r.status_code == 403
    assert "仅限管理员" in r.json()["detail"]


@pytest.mark.asyncio
async def test_runtime_denied_when_role_not_allowed(
    client, publisher_headers, admin_headers, user_headers
):
    """已加入但角色不匹配：403（文案含缺失角色）。"""
    cap_id = await _publish_tool(client, publisher_headers, admin_headers, "role-gate-tool")
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text

    await _set_access(client, admin_headers, cap_id, "restricted", allowed_roles=["publisher"])
    r = await client.post(
        "/api/runtime/tools/role-gate-tool/invoke", headers=user_headers, json={"params": {}}
    )
    assert r.status_code == 403
    assert "角色" in r.json()["detail"]
    assert "publisher" in r.json()["detail"]


@pytest.mark.asyncio
async def test_runtime_author_unrestricted_by_department(
    client, publisher_headers, admin_headers
):
    """作者不受部门门限制。"""
    cap_id = await _publish_tool(client, publisher_headers, admin_headers, "author-free-tool")
    await _set_access(client, admin_headers, cap_id, "restricted", allowed_departments=["研发部"])

    r = await client.post(
        "/api/runtime/tools/author-free-tool/invoke", headers=publisher_headers, json={"params": {}}
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_default_on_respects_access_predicate(
    client, publisher_headers, admin_headers, user_headers
):
    """default_on 自动加入尊重访问谓词：未命中不加入，命中后自动加入。"""
    cap_id = await _publish_tool(client, publisher_headers, admin_headers, "default-dept-tool")
    r = await client.post(
        f"/api/capabilities/{cap_id}/install-policy",
        headers=admin_headers,
        json={"install_policy": "default_on"},
    )
    assert r.status_code == 200, r.text
    await _set_access(client, admin_headers, cap_id, "restricted", allowed_departments=["研发部"])

    r = await client.get("/api/my/capabilities?scope=added", headers=user_headers)
    assert r.status_code == 200
    assert not any(c["id"] == cap_id for c in r.json())

    await _set_user_department("user", "研发部")
    r = await client.get("/api/my/capabilities?scope=added", headers=user_headers)
    assert r.status_code == 200
    assert any(c["id"] == cap_id for c in r.json())


@pytest.mark.asyncio
async def test_default_on_skips_invisible_capabilities(
    client, publisher_headers, admin_headers, user_headers
):
    """default_on + private/team 不可见：不自动加入；同团队可见者才加入。"""
    async with SessionLocal() as db:
        publisher = await db.scalar(select(User).where(User.username == "publisher"))
        private_cap = Capability(
            name="default-private-tool",
            type="tool",
            version="1.0.0",
            status="published",
            visibility="private",
            install_policy="default_on",
            author_id=publisher.id,
            organization=publisher.organization,
        )
        team_cap = Capability(
            name="default-team-tool",
            type="tool",
            version="1.0.0",
            status="published",
            visibility="team",
            install_policy="default_on",
            author_id=publisher.id,
            organization=publisher.organization,
        )
        teammate = User(
            username="default-teammate",
            email="default-teammate@example.com",
            password_hash=hash_password("teammate-secret-123"),
            role="user",
            team="中台团队",
        )
        db.add_all([private_cap, team_cap, teammate])
        await db.commit()
        private_id, team_id = private_cap.id, team_cap.id

    r = await client.get("/api/my/capabilities?scope=added", headers=user_headers)
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert private_id not in ids
    assert team_id not in ids

    r = await client.post(
        "/api/auth/login", json={"username": "default-teammate", "password": "teammate-secret-123"}
    )
    assert r.status_code == 200
    teammate_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.get("/api/my/capabilities?scope=added", headers=teammate_headers)
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert team_id in ids
    assert private_id not in ids


@pytest.mark.asyncio
async def test_default_on_plugin_joins_only_accessible_components(
    client, publisher_headers, admin_headers, user_headers
):
    """default_on plugin：只自动加入有权限的组件，无权限组件跳过。"""
    from test_plugin import _plugin_zip

    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "默认权限插件", "type": "plugin", "version": "1.0.0", "description": "x"},
    )
    assert r.status_code == 201, r.text
    plugin_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{plugin_id}/artifact",
        headers=publisher_headers,
        files={"file": ("p.zip", _plugin_zip(name="默认权限插件", version="1.0.0"), "application/zip")},
    )
    assert r.status_code == 200, r.text
    comps = r.json()["input_schema"]["components"]
    await client.post(f"/api/publish/capabilities/{plugin_id}/submit", headers=publisher_headers)
    await client.post(
        f"/api/admin/capabilities/{plugin_id}/review",
        headers=admin_headers,
        json={"action": "approve"},
    )
    r = await client.post(
        f"/api/capabilities/{plugin_id}/install-policy",
        headers=admin_headers,
        json={"install_policy": "default_on"},
    )
    assert r.status_code == 200, r.text

    blocked = next(c for c in comps if c["type"] == "skill")
    await _set_access(client, publisher_headers, blocked["capability_id"], "admin_only")

    r = await client.get(
        "/api/my/capabilities?scope=added&include_components=true", headers=user_headers
    )
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert plugin_id in ids
    assert blocked["capability_id"] not in ids
    for c in comps:
        if c["capability_id"] != blocked["capability_id"]:
            assert c["capability_id"] in ids
