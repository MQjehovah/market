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
    # 默认浏览：技能 + 安装包 + 助手（不含连接器/编排/编排函数）
    assert len(items) >= 1
    assert all(item["status"] == "published" for item in items)
    types = {item["type"] for item in items}
    assert types <= {"skill", "plugin", "agent"}
    assert "agent" in types or "plugin" in types or "skill" in types

    r_all = await client.get("/api/capabilities?include_bricks=true")
    assert r_all.status_code == 200
    all_types = {item["type"] for item in r_all.json()["items"]}
    assert {"agent", "tool", "skill", "mcp"} <= all_types


@pytest.mark.asyncio
async def test_register_login_flow(client):
    r = await client.post(
        "/api/auth/register",
        json={
            "username": "newbie",
            "email": "newbie@example.com",
            "password": "secret123",
            "display_name": "新人",
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["user"]["role"] == "user"
    token = r.json()["access_token"]
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    assert r.json()["username"] == "newbie"


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
        cap = await resolve_capability(db, teammate, "代码审查助手")
        assert cap.name == "代码审查助手"

    async with SessionLocal() as db:
        outsider = await db.scalar(select(User).where(User.username == "user"))
        with pytest.raises(HTTPException) as exc:
            await resolve_capability(db, outsider, "代码审查助手")
        assert exc.value.status_code == 404

    async with SessionLocal() as db:
        with pytest.raises(HTTPException) as exc:
            await resolve_capability(db, None, "代码审查助手")
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
