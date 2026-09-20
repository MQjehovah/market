"""Agent 一体化编辑测试：提示词 + 绑定能力 → 新版本草稿 → 发布 → 运行时组装。"""

import io
import json
import zipfile

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
        f"/api/runtime/agents/{persona}/instances", headers=admin_headers, json={"task": "x"}
    )
    assert r.status_code == 200, r.text
    runtime = r.json()["result"]["runtime"]
    assert any(t["name"] == dep_tool for t in runtime["tools"])


@pytest.mark.asyncio
async def test_normal_user_owner_can_edit_agent(client, user_headers):
    """普通用户作为所有者可在线保存助手（与 skill/mcp/tool 一致）。"""
    name = "user-owned-agent"
    r = await client.post(
        "/api/publish/capabilities",
        headers=user_headers,
        json={
            "name": name,
            "description": "用户自建助手",
            "type": "agent",
            "version": "0.1.0",
            "category": "测试",
            "tags": [],
            "visibility": "private",
        },
    )
    assert r.status_code == 201, r.text

    r = await client.get(f"/api/agents/{name}/edit", headers=user_headers)
    assert r.status_code == 200, r.text

    r = await client.put(
        f"/api/agents/{name}/edit",
        headers=user_headers,
        json={"prompt": "你是用户自建的测试助手。", "dependencies": []},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prompt"] == "你是用户自建的测试助手。"
    assert body["capability"]["status"] == "draft"
    draft_id = body["capability"]["id"]

    r = await client.get(f"/api/capabilities/{draft_id}", headers=user_headers)
    assert r.status_code == 200, r.text
    assert r.json().get("artifacts"), "在线保存应生成能力包"


@pytest.mark.asyncio
async def test_new_version_draft_inherits_prompt_for_edit(
    client, publisher_headers, admin_headers
):
    """详情页「创建新版本」不复制能力包；在线编辑应继承已发布 PROMPT.md / 绑定。"""
    persona = "inherit-persona"
    dep_tool = "inherit-dep-tool"
    await _publish_capability(
        client, publisher_headers, admin_headers, dep_tool, "tool", _tool_zip(dep_tool)
    )

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "agent.json",
            json.dumps({"name": persona, "description": "测试人设", "version": "1.0.0"}),
        )
        zf.writestr("PROMPT.md", "你是继承测试人设，负责回归。")
        zf.writestr(
            "dependencies.json",
            json.dumps([{"name": dep_tool, "type": "tool", "version": ""}], ensure_ascii=False),
        )
    cap_id = await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", buf.getvalue()
    )

    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/versions",
        headers=publisher_headers,
        json={"new_version": "1.0.1", "change_type": "patch"},
    )
    assert r.status_code == 201, r.text
    assert not r.json().get("artifacts")

    r = await client.get(f"/api/agents/{persona}/edit", headers=publisher_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability"]["version"] == "1.0.1"
    assert body["capability"]["status"] == "draft"
    assert body["prompt"] == "你是继承测试人设，负责回归。"
    assert body["dependencies"] == [{"name": dep_tool, "type": "tool", "version": ""}]
    assert body["base_version"] == "1.0.0"

    # 草稿若误存了空 PROMPT.md（且未带 dependencies），编辑态仍回落到已发布内容
    empty = io.BytesIO()
    with zipfile.ZipFile(empty, "w") as zf:
        zf.writestr("PROMPT.md", "")
        zf.writestr(
            "agent.json",
            json.dumps({"name": persona, "version": "1.0.1"}),
        )
    draft_id = body["capability"]["id"]
    r = await client.post(
        f"/api/publish/capabilities/{draft_id}/artifact",
        headers=publisher_headers,
        files={"file": ("empty.zip", empty.getvalue(), "application/zip")},
    )
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/agents/{persona}/edit", headers=publisher_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["prompt"] == "你是继承测试人设，负责回归。"
    assert body["dependencies"] == [{"name": dep_tool, "type": "tool", "version": ""}]