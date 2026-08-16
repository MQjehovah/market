"""能力调用权限：open / admin_only / restricted（白名单）。"""

import pytest

from test_workflow import _tool_zip, _publish_capability


async def _set_access(client, headers, cap_id, policy, allowed=None):
    r = await client.post(
        f"/api/capabilities/{cap_id}/access",
        headers=headers,
        json={"access_policy": policy, "allowed_users": allowed or []},
    )
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_admin_only_capability(client, publisher_headers, admin_headers, user_headers):
    name = "secret-tool"
    await _publish_capability(client, publisher_headers, admin_headers, name, "tool", _tool_zip(name))
    r = await client.get("/api/capabilities", params={"q": name})
    cap_id = r.json()["items"][0]["id"]
    await _set_access(client, admin_headers, cap_id, "admin_only")

    # 普通用户即使加入也不能调用
    await client.post("/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id})
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
    await client.post("/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id})
    r = await client.post(
        f"/api/runtime/tools/{name}/invoke",
        headers=user_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 200, r.text

    # 非白名单、非作者的用户被拒
    await client.post("/api/my/capabilities", headers=publisher_headers, json={"capability_id": cap_id})
    r = await client.post(
        f"/api/runtime/tools/{name}/invoke",
        headers=publisher_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 403
    assert "白名单" in r.json()["detail"]


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
