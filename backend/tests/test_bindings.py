"""Agent 动态绑定测试：绑定 CRUD + 实例化时按绑定/临时绑定组装运行时。"""

import pytest

from test_workflow import _agent_zip, _publish_capability, _tool_zip


@pytest.mark.asyncio
async def test_binding_crud_and_runtime_assembly(client, publisher_headers):
    r = await client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    admin_headers = {"Authorization": f"Bearer {r.json()['access_token']}"}

    persona = "bind-persona"
    dep_tool = "bind-dep-tool"
    await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona)
    )
    await _publish_capability(client, publisher_headers, admin_headers, dep_tool, "tool", _tool_zip(dep_tool))

    # 创建绑定
    r = await client.post(
        f"/api/agents/{persona}/bindings",
        headers=publisher_headers,
        json={
            "name": "默认绑定",
            "description": "测试",
            "version": "0.1.0",
            "dependencies": [{"name": dep_tool, "type": "tool", "version": ""}],
        },
    )
    assert r.status_code == 201, r.text
    binding = r.json()
    assert binding["agent_name"] == persona
    assert binding["dependencies"] == [{"name": dep_tool, "type": "tool", "version": ""}]

    # 列表与重复名校验
    r = await client.get(f"/api/agents/{persona}/bindings", headers=publisher_headers)
    assert r.status_code == 200
    assert len(r.json()) == 1
    r = await client.post(
        f"/api/agents/{persona}/bindings",
        headers=publisher_headers,
        json={"name": "默认绑定", "dependencies": []},
    )
    assert r.status_code == 409

    # 按绑定名实例化 → 运行时包含工具
    r = await client.post(
        f"/api/runtime/agents/{persona}/instances",
        headers=publisher_headers,
        json={"task": "测试", "binding": "默认绑定"},
    )
    assert r.status_code == 200, r.text
    runtime = r.json()["result"]["runtime"]
    assert any(t["name"] == dep_tool for t in runtime["tools"])
    assert r.json()["result"]["binding"] == "默认绑定"

    # 临时绑定（不落库）
    r = await client.post(
        f"/api/runtime/agents/{persona}/instances",
        headers=publisher_headers,
        json={"task": "x", "bindings": [{"name": dep_tool, "type": "tool"}]},
    )
    assert r.status_code == 200, r.text
    runtime = r.json()["result"]["runtime"]
    assert any(t["name"] == dep_tool for t in runtime["tools"])
    assert r.json()["result"]["binding"] == "adhoc"

    # 删除绑定后按名解析应 404
    r = await client.delete(
        f"/api/agents/{persona}/bindings/{binding['id']}", headers=publisher_headers
    )
    assert r.status_code == 204
    r = await client.post(
        f"/api/runtime/agents/{persona}/instances",
        headers=publisher_headers,
        json={"task": "x", "binding": "默认绑定"},
    )
    assert r.status_code == 404
