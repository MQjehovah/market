"""用户管理：管理员新增用户、启用/禁用、角色指定。"""

import pytest


@pytest.mark.asyncio
async def test_admin_create_user_role_and_toggle(client, admin_headers, user_headers):
    # 管理员新增发布者
    r = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "username": "newpublisher",
            "email": "newpublisher@example.com",
            "password": "secret123",
            "display_name": "新发布者",
            "organization": "平台部",
            "role": "publisher",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["role"] == "publisher"
    assert body["is_active"] is True
    uid = body["id"]

    # 新用户可登录（role=publisher）
    r = await client.post(
        "/api/auth/login", json={"username": "newpublisher", "password": "secret123"}
    )
    assert r.status_code == 200

    # 禁用后无法登录，且响应带 is_active=false
    r = await client.patch(
        f"/api/admin/users/{uid}", headers=admin_headers, json={"is_active": False}
    )
    assert r.status_code == 200, r.text
    assert r.json()["is_active"] is False
    r = await client.post(
        "/api/auth/login", json={"username": "newpublisher", "password": "secret123"}
    )
    assert r.status_code == 403

    # 再启用
    r = await client.patch(
        f"/api/admin/users/{uid}", headers=admin_headers, json={"is_active": True}
    )
    assert r.status_code == 200
    assert r.json()["is_active"] is True

    # 重复用户名/邮箱冲突
    r = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "newpublisher", "email": "other@example.com", "password": "secret123"},
    )
    assert r.status_code == 409

    # 普通用户无权新增
    r = await client.post(
        "/api/admin/users",
        headers=user_headers,
        json={"username": "hacker", "email": "h@example.com", "password": "secret123"},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_admin_update_user_and_reset_password(client, admin_headers):
    r = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "username": "editme",
            "email": "editme@example.com",
            "password": "oldpass123",
            "display_name": "旧名字",
            "role": "user",
        },
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]

    # 修改资料 + 重置密码
    r = await client.patch(
        f"/api/admin/users/{uid}",
        headers=admin_headers,
        json={"display_name": "新名字", "email": "edited@example.com", "password": "newpass456"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["display_name"] == "新名字"
    assert r.json()["email"] == "edited@example.com"

    # 旧密码登录失败、新密码成功
    r = await client.post("/api/auth/login", json={"username": "editme", "password": "oldpass123"})
    assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"username": "editme", "password": "newpass456"})
    assert r.status_code == 200

    # 用户名/邮箱冲突
    r = await client.patch(
        f"/api/admin/users/{uid}",
        headers=admin_headers,
        json={"username": "admin"},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_self_change_password(client, admin_headers):
    # 创建临时用户后自助改密
    r = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "selfpw", "email": "selfpw@example.com", "password": "first123"},
    )
    assert r.status_code == 201
    r = await client.post("/api/auth/login", json={"username": "selfpw", "password": "first123"})
    headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    # 原密码错误
    r = await client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": "wrong", "new_password": "second456"},
    )
    assert r.status_code == 401

    # 正确修改
    r = await client.post(
        "/api/auth/change-password",
        headers=headers,
        json={"old_password": "first123", "new_password": "second456"},
    )
    assert r.status_code == 200, r.text
    r = await client.post("/api/auth/login", json={"username": "selfpw", "password": "first123"})
    assert r.status_code == 401
    r = await client.post("/api/auth/login", json={"username": "selfpw", "password": "second456"})
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_admin_delete_user(client, admin_headers, publisher_headers):
    # 无能力的用户可删除
    r = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "todelete", "email": "todelete@example.com", "password": "delete123"},
    )
    assert r.status_code == 201
    uid = r.json()["id"]
    r = await client.delete(f"/api/admin/users/{uid}", headers=admin_headers)
    assert r.status_code == 200, r.text
    r = await client.post("/api/auth/login", json={"username": "todelete", "password": "delete123"})
    assert r.status_code == 401
    r = await client.delete(f"/api/admin/users/{uid}", headers=admin_headers)
    assert r.status_code == 404

    # 不能删除自己
    r = await client.get("/api/admin/users", headers=admin_headers)
    me = next(u for u in r.json() if u["username"] == "admin")
    r = await client.delete(f"/api/admin/users/{me['id']}", headers=admin_headers)
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_delete_user_transfers_capabilities(client, admin_headers):
    """删除发布过能力的用户：能力自动转移给当前管理员。"""
    r = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={"username": "transferme", "email": "transferme@example.com", "password": "transfer123"},
    )
    assert r.status_code == 201
    uid = r.json()["id"]

    # 临时用户发布一个能力
    r = await client.post(
        "/api/auth/login", json={"username": "transferme", "password": "transfer123"}
    )
    user_tok = {"Authorization": f"Bearer {r.json()['access_token']}"}
    r = await client.post(
        "/api/publish/capabilities",
        headers=user_tok,
        json={"name": "transfer-cap", "description": "转移测试", "type": "tool", "version": "1.0.0"},
    )
    assert r.status_code == 201
    cap_id = r.json()["id"]
    # 提交审核前需上传能力包；本用例只关心删除用户后作者转移，草稿即可
    await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=user_tok)
    await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve"},
    )

    # 删除临时用户 → 能力转移给 admin
    r = await client.delete(f"/api/admin/users/{uid}", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert "转移" in r.json()["message"]

    r = await client.get("/api/admin/users", headers=admin_headers)
    admin_id = next(u for u in r.json() if u["username"] == "admin")["id"]
    r = await client.get(f"/api/capabilities/{cap_id}", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["author_id"] == admin_id

    # 删除后原用户无法登录
    r = await client.post(
        "/api/auth/login", json={"username": "transferme", "password": "transfer123"}
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_cannot_disable_or_demote_self(client, admin_headers):
    r = await client.get("/api/admin/users", headers=admin_headers)
    me = next(u for u in r.json() if u["username"] == "admin")
    uid = me["id"]

    # 不能禁用自己
    r = await client.patch(
        f"/api/admin/users/{uid}", headers=admin_headers, json={"is_active": False}
    )
    assert r.status_code == 409
    assert "当前登录" in r.json()["detail"]

    # 不能把自己降级为普通用户（系统至少保留一个管理员）
    r = await client.patch(
        f"/api/admin/users/{uid}", headers=admin_headers, json={"role": "user"}
    )
    assert r.status_code == 409

    # 管理员账号仍可用
    r = await client.post("/api/auth/login", json={"username": "admin", "password": "test-seed-admin-password-32-bytes!!"})
    assert r.status_code == 200
