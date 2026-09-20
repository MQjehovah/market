"""我的能力：加入 / 列表 / 移除，以及「作者或加入者可调用」的授权模型。"""

import pytest

from test_workflow import _tool_zip, _publish_capability


@pytest.mark.asyncio
async def test_user_adds_capability_then_can_invoke(client, publisher_headers, admin_headers, user_headers):
    tool_name = "my-tool"
    await _publish_capability(client, publisher_headers, admin_headers, tool_name, "tool", _tool_zip(tool_name))

    r = await client.get("/api/capabilities", params={"q": tool_name})
    cap_id = r.json()["items"][0]["id"]

    # 未加入前：普通用户调用被拒
    r = await client.post(
        f"/api/runtime/tools/{tool_name}/invoke",
        headers=user_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 403, r.text

    # 加入我的能力
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text

    # 列表中出现，且 added=True
    r = await client.get("/api/my/capabilities", headers=user_headers)
    assert r.status_code == 200
    mine = [c for c in r.json() if c["id"] == cap_id]
    assert mine and mine[0]["added"] is True and mine[0]["owned"] is False

    # 加入后：普通用户可调用（作者/加入者通道）
    r = await client.post(
        f"/api/runtime/tools/{tool_name}/invoke",
        headers=user_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 200, r.text
    assert r.json()["result"]["status"] == "ok"

    # 移除后：再次调用被拒
    r = await client.delete(f"/api/my/capabilities/{cap_id}", headers=user_headers)
    assert r.status_code == 200
    r = await client.post(
        f"/api/runtime/tools/{tool_name}/invoke",
        headers=user_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_owner_can_invoke_without_joining(client, publisher_headers, admin_headers):
    """能力作者无需加入即可调用自己的能力。"""
    tool_name = "owner-invoke-tool"
    await _publish_capability(client, publisher_headers, admin_headers, tool_name, "tool", _tool_zip(tool_name))
    r = await client.post(
        f"/api/runtime/tools/{tool_name}/invoke",
        headers=publisher_headers,
        json={"params": {"text": "hi"}},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_install_policy_default_on_and_required(
    client, publisher_headers, admin_headers, user_headers
):
    """default_on 登录/拉列表时自动加入；required 禁止移除。"""
    auto_name = "auto-join-tool"
    must_name = "must-keep-tool"
    await _publish_capability(
        client, publisher_headers, admin_headers, auto_name, "tool", _tool_zip(auto_name)
    )
    await _publish_capability(
        client, publisher_headers, admin_headers, must_name, "tool", _tool_zip(must_name)
    )
    r = await client.get("/api/capabilities", params={"q": auto_name})
    auto_id = r.json()["items"][0]["id"]
    r = await client.get("/api/capabilities", params={"q": must_name})
    must_id = r.json()["items"][0]["id"]

    r = await client.post(
        f"/api/capabilities/{auto_id}/install-policy",
        headers=admin_headers,
        json={"install_policy": "default_on"},
    )
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/capabilities/{must_id}/install-policy",
        headers=admin_headers,
        json={"install_policy": "required"},
    )
    assert r.status_code == 200, r.text

    # 拉「我的能力」会同步 default_on
    r = await client.get("/api/my/capabilities?scope=added", headers=user_headers)
    assert r.status_code == 200
    assert any(c["id"] == auto_id for c in r.json())

    # required 也可手动加入，但不可移除
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": must_id}
    )
    assert r.status_code == 201, r.text
    r = await client.get("/api/my/capabilities", headers=user_headers)
    must_row = next(c for c in r.json() if c["id"] == must_id)
    assert must_row.get("removable") is False
    r = await client.delete(f"/api/my/capabilities/{must_id}", headers=user_headers)
    assert r.status_code == 422
    assert "必装" in r.json()["detail"]


@pytest.mark.asyncio
async def test_my_capabilities_scope_and_join_guard(client, publisher_headers, admin_headers, user_headers):
    tool_name = "scope-tool"
    await _publish_capability(client, publisher_headers, admin_headers, tool_name, "tool", _tool_zip(tool_name))

    # 未发布的草稿不能加入
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": "draft-tool",
            "description": "草稿",
            "type": "tool",
            "version": "0.1.0",
            "category": "测试",
            "tags": [],
            "visibility": "internal",
        },
    )
    draft_id = r.json()["id"]
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": draft_id}
    )
    assert r.status_code == 422

    r = await client.get("/api/capabilities", params={"q": tool_name})
    cap_id = r.json()["items"][0]["id"]
    await client.post("/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id})

    # 发布者自己的能力在 owned 里
    r = await client.get("/api/my/capabilities?scope=owned", headers=publisher_headers)
    names = [c["name"] for c in r.json()]
    assert tool_name in names

    # 普通用户的 added 里有，owned 里没有
    r = await client.get("/api/my/capabilities?scope=added", headers=user_headers)
    assert any(c["name"] == tool_name for c in r.json())
    r = await client.get("/api/my/capabilities?scope=owned", headers=user_headers)
    assert not any(c["name"] == tool_name for c in r.json())
