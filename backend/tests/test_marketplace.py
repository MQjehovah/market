"""核心流程测试：认证 / 发布审核 / 权限矩阵 / 搜索 / 执行引擎 / 评分订阅。"""

import io
import json
import zipfile

import pytest


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
    # 默认货架：安装包 + 配方（不含积木）
    assert len(items) >= 1
    assert all(item["status"] == "published" for item in items)
    types = {item["type"] for item in items}
    assert types <= {"plugin", "agent", "workflow"}
    assert "agent" in types

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
