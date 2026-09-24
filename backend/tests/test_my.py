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


@pytest.mark.asyncio
async def test_host_sync_enabled_only(client, publisher_headers, admin_headers, user_headers):
    """宿主同步只返回已加入且启用、有包（或 tool）的项。"""
    from test_skill_edit import _skill_zip

    name = "host-sync-skill"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "skill", _skill_zip(name)
    )
    r = await client.get("/api/capabilities", params={"q": name, "type": "skill"})
    cap_id = r.json()["items"][0]["id"]

    r = await client.get("/api/my/host-sync", headers=user_headers)
    assert r.status_code == 200
    assert not any(i["name"] == name for i in r.json()["items"])

    await client.post("/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id})
    r = await client.get("/api/my/host-sync", headers=user_headers)
    assert r.status_code == 200
    items = [i for i in r.json()["items"] if i["name"] == name]
    assert len(items) == 1
    assert items[0]["enabled"] is True
    assert items[0]["type"] == "skill"
    assert "download_url" in items[0] and name in items[0]["download_url"]
    assert "consumers" in items[0]

    r = await client.patch(
        f"/api/my/capabilities/{cap_id}",
        headers=user_headers,
        json={"enabled": False},
    )
    assert r.status_code == 200
    r = await client.get("/api/my/host-sync", headers=user_headers)
    assert not any(i["name"] == name for i in r.json()["items"])


@pytest.mark.asyncio
async def test_join_agent_also_joins_dependencies(
    client, publisher_headers, admin_headers, user_headers
):
    """加入助手时，dependencies.json 里已上架的 skill/tool 一并进入「我的能力」。"""
    import io
    import json
    import zipfile

    from test_skill_edit import _skill_zip
    from test_workflow import _publish_capability, _tool_zip

    skill_name = "join-dep-skill"
    tool_name = "join-dep-tool"
    agent_name = "join-dep-agent"
    await _publish_capability(
        client, publisher_headers, admin_headers, skill_name, "skill", _skill_zip(skill_name)
    )
    await _publish_capability(
        client, publisher_headers, admin_headers, tool_name, "tool", _tool_zip(tool_name)
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "agent.json",
            json.dumps(
                {"name": agent_name, "description": "join deps", "version": "1.0.0"},
                ensure_ascii=False,
            ),
        )
        zf.writestr("PROMPT.md", f"你是{agent_name}。")
        zf.writestr(
            "dependencies.json",
            json.dumps(
                [
                    {"name": skill_name, "type": "skill", "version": "1.0.0"},
                    {"name": tool_name, "type": "tool", "version": ""},
                ],
                ensure_ascii=False,
            ),
        )
    await _publish_capability(
        client, publisher_headers, admin_headers, agent_name, "agent", buf.getvalue()
    )

    r = await client.get("/api/capabilities", params={"type": "agent", "q": agent_name})
    assert r.status_code == 200
    agent_id = next(c["id"] for c in r.json()["items"] if c["name"] == agent_name)

    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": agent_id}
    )
    assert r.status_code == 201, r.text
    assert "依赖" in r.json()["message"]

    r = await client.get("/api/my/capabilities", headers=user_headers, params={"scope": "added"})
    assert r.status_code == 200
    names = {c["name"] for c in r.json() if c.get("added")}
    assert agent_name in names
    assert skill_name in names
    assert tool_name in names


@pytest.mark.asyncio
async def test_join_invisible_draft_returns_404(client, publisher_headers, user_headers):
    """他人不可见的草稿：先判可见性，返回 404 而非 422（不泄露状态）。"""
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "私密草稿", "type": "tool", "version": "0.1.0", "visibility": "private"},
    )
    assert r.status_code == 201, r.text
    draft_id = r.json()["id"]

    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": draft_id}
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_join_agent_skips_denied_dependency(
    client, publisher_headers, admin_headers, user_headers
):
    """助手依赖无权限时：主能力加入成功，文案按「依赖」提示跳过数。"""
    import io
    import json
    import zipfile

    from test_skill_edit import _skill_zip
    from test_workflow import _publish_capability, _tool_zip

    skill_name = "join-skip-skill"
    tool_name = "join-skip-tool"
    agent_name = "join-skip-agent"
    await _publish_capability(
        client, publisher_headers, admin_headers, skill_name, "skill", _skill_zip(skill_name)
    )
    await _publish_capability(
        client, publisher_headers, admin_headers, tool_name, "tool", _tool_zip(tool_name)
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "agent.json",
            json.dumps(
                {"name": agent_name, "description": "join skip", "version": "1.0.0"},
                ensure_ascii=False,
            ),
        )
        zf.writestr("PROMPT.md", f"你是{agent_name}。")
        zf.writestr(
            "dependencies.json",
            json.dumps(
                [
                    {"name": skill_name, "type": "skill", "version": "1.0.0"},
                    {"name": tool_name, "type": "tool", "version": ""},
                ],
                ensure_ascii=False,
            ),
        )
    await _publish_capability(
        client, publisher_headers, admin_headers, agent_name, "agent", buf.getvalue()
    )

    r = await client.get("/api/capabilities", params={"q": agent_name})
    agent_id = next(c["id"] for c in r.json()["items"] if c["name"] == agent_name)
    r = await client.get("/api/capabilities", params={"q": skill_name})
    skill_id = next(c["id"] for c in r.json()["items"] if c["name"] == skill_name)
    r = await client.get("/api/capabilities", params={"q": tool_name})
    tool_id = next(c["id"] for c in r.json()["items"] if c["name"] == tool_name)

    # 收紧 skill 为 admin_only（作者=publisher）
    r = await client.post(
        f"/api/capabilities/{skill_id}/access",
        headers=publisher_headers,
        json={"access_policy": "admin_only"},
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": agent_id}
    )
    assert r.status_code == 201, r.text
    assert "1 个依赖因权限不足未加入" in r.json()["message"]

    r = await client.get(
        "/api/my/capabilities?scope=added&include_components=true", headers=user_headers
    )
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert agent_id in ids
    assert tool_id in ids
    assert skill_id not in ids
