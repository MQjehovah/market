"""组装 Agent + 工作流执行引擎测试。"""

import io
import json
import zipfile

import pytest


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


def _tool_zip(name: str) -> bytes:
    return _zip(
        {
            "tool.json": json.dumps(
                {"name": name, "description": "测试工具", "version": "1.0.0"}
            ).encode("utf-8"),
            "schema.json": json.dumps(
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                }
            ).encode("utf-8"),
            "implementation/tool.py": (
                "def run(params):\n"
                "    return {'ok': True, 'output': 'echo:' + str(params.get('text', ''))}\n"
            ).encode("utf-8"),
        }
    )


def _agent_zip(name: str) -> bytes:
    return _zip(
        {
            "agent.json": json.dumps(
                {"name": name, "description": "测试人设", "version": "1.0.0"}
            ).encode("utf-8"),
            "PROMPT.md": f"你是{name}，负责测试。".encode("utf-8"),
        }
    )


async def _publish_capability(
    client, publisher_headers, admin_headers, name: str, type_: str, pkg: bytes
) -> str:
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "description": "测试",
            "type": type_,
            "version": "1.0.0",
            "category": "测试",
            "tags": [],
            "visibility": "internal",
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", pkg, "application/zip")},
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    assert r.status_code == 200
    r = await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    return cap_id


@pytest.mark.asyncio
async def test_workflow_create_execute_query(client, publisher_headers, admin_headers):
    tool_name = "wf-echo-tool"
    await _publish_capability(client, publisher_headers, admin_headers, tool_name, "tool", _tool_zip(tool_name))

    wf_name = "wf-demo"
    workflow = {
        "name": wf_name,
        "description": "测试工作流",
        "version": "1.0.0",
        "nodes": [
            {
                "id": "t1",
                "type": "tool",
                "capability": tool_name,
                "params": {"text": "hello ${input.name}"},
            }
        ],
        "edges": [],
    }
    r = await client.post(
        "/api/workflows",
        headers=publisher_headers,
        json={
            "name": wf_name,
            "description": "测试工作流",
            "version": "1.0.0",
            "category": "工作流",
            "tags": ["测试"],
            "workflow": workflow,
        },
    )
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]
    r = await client.post(f"/api/publish/capabilities/{wf_id}/submit", headers=publisher_headers)
    assert r.status_code == 200
    r = await client.post(
        f"/api/admin/capabilities/{wf_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text

    r = await client.post(
        f"/api/runtime/workflows/{wf_name}/executions",
        headers=admin_headers,
        json={"input": {"name": "world"}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "succeeded", body
    assert body["node_states"]["t1"] == "succeeded"
    assert body["outputs"]["t1"]["status"] == "ok"
    assert "echo:hello world" in json.dumps(body["outputs"]["t1"], ensure_ascii=False)

    r = await client.get(
        f"/api/runtime/workflows/executions/{body['id']}", headers=admin_headers
    )
    assert r.status_code == 200
    assert r.json()["workflow_name"] == wf_name


@pytest.mark.asyncio
async def test_workflow_cycle_rejected(client, publisher_headers):
    workflow = {
        "nodes": [
            {"id": "a", "type": "tool", "capability": "x", "params": {}},
            {"id": "b", "type": "tool", "capability": "y", "params": {}},
        ],
        "edges": [{"from": "a", "to": "b"}, {"from": "b", "to": "a"}],
    }
    r = await client.post(
        "/api/workflows",
        headers=publisher_headers,
        json={"name": "wf-cycle", "version": "1.0.0", "workflow": workflow},
    )
    assert r.status_code == 422
    assert "环" in r.json()["detail"]


@pytest.mark.asyncio
async def test_workflow_definition_read_update_test(
    client, publisher_headers, admin_headers, user_headers
):
    """草稿定义可读回、可更新、可试运行；非作者不可读草稿。"""
    tool_name = "wf-draft-tool"
    await _publish_capability(
        client, publisher_headers, admin_headers, tool_name, "tool", _tool_zip(tool_name)
    )

    workflow = {
        "nodes": [
            {
                "id": "t1",
                "type": "tool",
                "capability": tool_name,
                "params": {"text": "hi ${input.name}"},
            }
        ],
        "edges": [],
    }
    r = await client.post(
        "/api/workflows",
        headers=publisher_headers,
        json={
            "name": "wf-draft-demo",
            "description": "草稿工作流",
            "version": "0.1.0",
            "workflow": workflow,
        },
    )
    assert r.status_code == 201, r.text
    wf_id = r.json()["id"]

    # 非作者不能读草稿定义
    r = await client.get(f"/api/workflows/{wf_id}/definition", headers=user_headers)
    assert r.status_code == 403

    # 作者可读回画布
    r = await client.get(
        f"/api/workflows/{wf_id}/definition", headers=publisher_headers
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["workflow"]["nodes"][0]["id"] == "t1"
    assert body["capability"]["status"] == "draft"

    # 更新画布：追加第二个工具节点并连线
    updated = {
        "nodes": [
            {
                "id": "t1",
                "type": "tool",
                "capability": tool_name,
                "params": {"text": "hello ${input.name}"},
            },
            {
                "id": "t2",
                "type": "tool",
                "capability": tool_name,
                "params": {"text": "${t1.output}"},
            },
        ],
        "edges": [{"from": "t1", "to": "t2"}],
    }
    r = await client.put(
        f"/api/workflows/{wf_id}", headers=publisher_headers, json={"workflow": updated}
    )
    assert r.status_code == 200, r.text

    r = await client.get(
        f"/api/workflows/{wf_id}/definition", headers=publisher_headers
    )
    assert r.status_code == 200
    assert len(r.json()["workflow"]["nodes"]) == 2

    # 试运行草稿
    r = await client.post(
        f"/api/workflows/{wf_id}/test",
        headers=publisher_headers,
        json={"input": {"name": "画布"}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "succeeded", body
    assert body["node_states"]["t1"] == "succeeded"

    # 已提交后不可再更新画布
    r = await client.post(f"/api/publish/capabilities/{wf_id}/submit", headers=publisher_headers)
    assert r.status_code == 200
    r = await client.put(
        f"/api/workflows/{wf_id}", headers=publisher_headers, json={"workflow": updated}
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_assemble_agent_package(client, publisher_headers, admin_headers):
    persona = "assemble-persona"
    dep_tool = "assemble-dep-tool"
    await _publish_capability(client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona))
    await _publish_capability(client, publisher_headers, admin_headers, dep_tool, "tool", _tool_zip(dep_tool))

    r = await client.post(
        "/api/assemble/agents",
        headers=publisher_headers,
        json={
            "persona": persona,
            "name": "assemble-demo",
            "version": "0.1.0",
            "description": "组装测试",
            "dependencies": [{"name": dep_tool, "type": "tool"}],
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["type"] == "agent"
    assert body["status"] == "draft"

    # 作者可通过 publish 下载接口拿到组装产物并校验内容
    r = await client.get(
        f"/api/publish/capabilities/{body['id']}/artifact/download",
        headers=publisher_headers,
    )
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
        assert {"agent.json", "PROMPT.md", "dependencies.json", "tools.json"} <= names
        assert f"tools/{dep_tool}/tool.py" in names
        manifest = json.loads(zf.read("dependencies.json"))
    assert manifest == [{"name": dep_tool, "type": "tool", "version": "1.0.0"}]
