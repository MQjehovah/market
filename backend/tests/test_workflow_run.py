"""工作流"作为应用"运行：run-meta / 表单必填与默认 / 权限放宽 / 执行记录。"""

import pytest

from app.services.workflows import _agent_extra_deps, extract_run_meta, normalize_start_fields


def test_agent_extra_deps_mapping():
    deps = _agent_extra_deps(
        {"skills": ["weekly-report", {"name": "report-writer"}], "tools": ["web_search"], "mcps": ["erp"]}
    )
    assert {"name": "weekly-report", "type": "skill"} in deps
    assert {"name": "report-writer", "type": "skill"} in deps
    assert {"name": "web_search", "type": "tool"} in deps
    assert {"name": "erp", "type": "mcp"} in deps
    assert _agent_extra_deps({}) == []


def test_normalize_start_fields_mixed():
    out = normalize_start_fields(["a", {"key": "b", "label": "标题B", "required": True, "type": "number"}])
    assert out[0]["key"] == "a" and out[0]["type"] == "text"
    assert out[1] == {
        "key": "b", "label": "标题B", "type": "number", "required": True,
        "default": "", "options": [], "placeholder": "",
    }
    assert normalize_start_fields(None) == []


def test_extract_run_meta_shape_inference():
    chat = {"mode": "chat", "nodes": [{"id": "s", "type": "start", "params": {"fields": []}}]}
    assert extract_run_meta(chat)["shape"] == "chat"
    form = {"nodes": [{"id": "s", "type": "start", "params": {"fields": ["x"]}}]}
    assert extract_run_meta(form)["shape"] == "form"
    auto = {"trigger": {"type": "schedule", "cron": "* * * * *"}, "nodes": [{"id": "s", "type": "start"}]}
    assert extract_run_meta(auto)["shape"] == "automation"
    # 显式开关优先
    explicit = {"presentation": {"mode": "form"}, "mode": "chat", "nodes": [{"id": "s", "type": "start"}]}
    assert extract_run_meta(explicit)["shape"] == "form"


def _wf(name: str, access_policy: str = "open") -> dict:
    return {
        "name": name,
        "presentation": {"mode": "form"},
        "nodes": [
            {
                "id": "start",
                "type": "start",
                "params": {
                    "fields": [
                        {"key": "name", "label": "名字", "required": True},
                        {"key": "greet", "label": "问候", "default": "你好"},
                    ]
                },
            },
            {"id": "t", "type": "template", "params": {"template": "${start.greet} ${start.name}"}},
            {"id": "end", "type": "end", "params": {"outputs": {"text": "${t.text}"}}},
        ],
        "edges": [{"from": "start", "to": "t"}, {"from": "t", "to": "end"}],
        "_access_policy": access_policy,
    }


async def _publish(client, headers, admin_headers, defn: dict) -> str:
    name = defn["name"]
    access = defn.pop("_access_policy", "open")
    r = await client.post(
        "/api/workflows",
        headers=headers,
        json={
            "name": name,
            "description": "app test",
            "version": "1.0.0",
            "category": "能力编排",
            "tags": [],
            "visibility": "internal",
            "access_policy": access,
            "workflow": defn,
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    assert (await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=headers)).status_code == 200
    r = await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    return cap_id


@pytest.mark.asyncio
async def test_app_run_form_and_executions(client, publisher_headers, admin_headers, user_headers):
    name = "app-demo"
    await _publish(client, publisher_headers, admin_headers, _wf(name))

    # 普通员工可见 run-meta（open 放宽）
    r = await client.get(f"/api/runtime/workflows/{name}/run-meta", headers=user_headers)
    assert r.status_code == 200, r.text
    meta = r.json()
    assert meta["shape"] == "form"
    keys = [f["key"] for f in meta["input_fields"]]
    assert keys == ["name", "greet"]
    assert meta["input_fields"][0]["required"] is True

    # 缺必填 → 422
    r = await client.post(f"/api/runtime/workflows/{name}/run", headers=user_headers, json={"input": {}})
    assert r.status_code == 422
    assert "名字" in r.json()["detail"]

    # 默认值生效 + 运行成功
    r = await client.post(
        f"/api/runtime/workflows/{name}/run", headers=user_headers, json={"input": {"name": "张三"}}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "succeeded", body
    assert body["outputs"]["end"]["text"] == "你好 张三"

    # 执行记录（本人）
    r = await client.get(f"/api/runtime/workflows/{name}/executions", headers=user_headers)
    assert r.status_code == 200
    assert len(r.json()) >= 1


@pytest.mark.asyncio
async def test_app_admin_only_denied(client, publisher_headers, admin_headers, user_headers):
    name = "app-admin-only"
    await _publish(client, publisher_headers, admin_headers, _wf(name, access_policy="admin_only"))
    r = await client.get(f"/api/runtime/workflows/{name}/run-meta", headers=user_headers)
    assert r.status_code == 403


def test_validate_workflow_node_requires_capability():
    from app.services.workflows import validate_definition

    with pytest.raises(Exception):
        validate_definition({"nodes": [{"id": "w", "type": "workflow"}]})


def _child_wf(name: str) -> dict:
    return {
        "name": name,
        "nodes": [
            {"id": "start", "type": "start", "params": {"fields": ["msg"]}},
            {"id": "t", "type": "template", "params": {"template": "child:${start.msg}"}},
            {"id": "end", "type": "end", "params": {"outputs": {"text": "${t.text}"}}},
        ],
        "edges": [{"from": "start", "to": "t"}, {"from": "t", "to": "end"}],
    }


def _parent_wf(name: str, child: str) -> dict:
    return {
        "name": name,
        "nodes": [
            {"id": "start", "type": "start", "params": {"fields": ["msg"]}},
            {"id": "wn", "type": "workflow", "capability": child, "params": {"input": {"msg": "${start.msg}"}}},
            {
                "id": "end",
                "type": "end",
                "params": {"outputs": {"child_text": "${wn.outputs.end.text}", "child_state": "${wn.state}"}},
            },
        ],
        "edges": [{"from": "start", "to": "wn"}, {"from": "wn", "to": "end"}],
    }


@pytest.mark.asyncio
async def test_workflow_as_tool(client, publisher_headers, admin_headers):
    child, parent = "wft-child", "wft-parent"
    await _publish(client, publisher_headers, admin_headers, _child_wf(child))
    await _publish(client, publisher_headers, admin_headers, _parent_wf(parent, child))

    r = await client.post(
        f"/api/runtime/workflows/{parent}/run", headers=admin_headers, json={"input": {"msg": "hi"}}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "succeeded", body
    assert body["outputs"]["end"]["child_text"] == "child:hi"
    assert body["outputs"]["end"]["child_state"] == "succeeded"
    assert body["node_outputs"]["wn"]["state"] == "succeeded"


@pytest.mark.asyncio
async def test_workflow_recursion_guard(client, publisher_headers):
    name = "wft-self"
    wf = {
        "name": name,
        "nodes": [
            {"id": "start", "type": "start"},
            {"id": "wn", "type": "workflow", "capability": name, "params": {}},
        ],
        "edges": [{"from": "start", "to": "wn"}],
    }
    r = await client.post(
        "/api/workflows", headers=publisher_headers, json={"name": name, "version": "1.0.0", "workflow": wf}
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(f"/api/workflows/{cap_id}/test", headers=publisher_headers, json={"input": {}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "failed"
    assert "递归" in (body.get("error") or "")
