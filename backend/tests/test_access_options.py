"""白名单选项接口: /api/meta/access-options 登录可读, 提供用户与部门候选。"""
import pytest


@pytest.mark.asyncio
async def test_access_options_requires_login(client):
    r = await client.get("/api/meta/access-options")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_access_options_lists_users_and_departments(client, user_headers):
    r = await client.get("/api/meta/access-options", headers=user_headers)
    assert r.status_code == 200, r.text
    data = r.json()
    assert isinstance(data["users"], list) and data["users"]
    usernames = {u["username"] for u in data["users"]}
    assert "admin" in usernames
    assert all({"username", "name", "department"} <= set(u) for u in data["users"])
    assert isinstance(data["departments"], list)
