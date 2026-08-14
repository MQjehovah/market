"""Agent 一体化编辑测试：提示词 + 绑定能力 → 新版本草稿 → 发布 → 运行时组装。"""

import pytest

from test_workflow import _agent_zip, _publish_capability, _tool_zip


@pytest.mark.asyncio
async def test_agent_edit_prompt_and_deps_versioned(client, publisher_headers, admin_headers):
    persona = "edit-persona"
    dep_tool = "edit-dep-tool"
    await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona)
    )
    await _publish_capability(
        client, publisher_headers, admin_headers, dep_tool, "tool", _tool_zip(dep_tool)
    )

    # 读取编辑态：最新已发布版本的提示词与依赖
    r = await client.get(f"/api/agents/{persona}/edit", headers=publisher_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability"]["version"] == "1.0.0"
    assert body["prompt"].startswith("你是")
    assert body["dependencies"] == []

    # 保存 = 新建版本草稿（patch+1），提示词与绑定能力一起进包
    r = await client.put(
        f"/api/agents/{persona}/edit",
        headers=publisher_headers,
        json={
            "prompt": "你是升级后的测试人设。",
            "dependencies": [{"name": dep_tool, "type": "tool", "version": ""}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability"]["version"] == "1.0.1"
    assert body["capability"]["status"] == "draft"
    assert body["prompt"] == "你是升级后的测试人设。"
    assert body["dependencies"] == [{"name": dep_tool, "type": "tool", "version": ""}]
    draft_id = body["capability"]["id"]

    # 再次保存 = 更新草稿，版本不变
    r = await client.put(
        f"/api/agents/{persona}/edit",
        headers=publisher_headers,
        json={
            "prompt": "你是最终版人设。",
            "dependencies": [{"name": dep_tool, "type": "tool", "version": "1.0.0"}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability"]["id"] == draft_id
    assert body["capability"]["version"] == "1.0.1"
    assert body["prompt"] == "你是最终版人设。"

    # 发布：提交审核 → 通过
    r = await client.post(
        f"/api/publish/capabilities/{draft_id}/submit", headers=publisher_headers
    )
    assert r.status_code == 200
    r = await client.post(
        f"/api/admin/capabilities/{draft_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"

    # 实例化：按包内 dependencies.json 动态组装
    r = await client.post(
        f"/api/runtime/agents/{persona}/instances", headers=publisher_headers, json={"task": "x"}
    )
    assert r.status_code == 200, r.text
    runtime = r.json()["result"]["runtime"]
    assert any(t["name"] == dep_tool for t in runtime["tools"])
