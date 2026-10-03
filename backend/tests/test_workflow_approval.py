"""工作流审批闸门：暂停等待 → 通过/驳回 → durable resume。"""

import pytest

from app.services.workflow_samples import CHANGE_APPROVAL_WORKFLOW
from app.services.workflows import _canon_type, validate_definition


def _approval_workflow() -> dict:
    return {
        "name": "approval-demo",
        "description": "审批测试",
        "on_error": "continue",
        "nodes": [
            {"id": "start", "type": "start", "params": {"fields": []}},
            {"id": "pre", "type": "template", "params": {"template": "prepared"}},
            {"id": "approval", "type": "approval", "params": {"title": "approve?", "assignee": "ops"}},
            {"id": "done", "type": "template", "params": {"template": "done:${approval.approved}"}},
        ],
        "edges": [
            {"from": "start", "to": "pre"},
            {"from": "pre", "to": "approval"},
            {"from": "approval", "to": "done", "condition": "true"},
        ],
    }


def test_approval_sample_and_alias_valid():
    order = validate_definition(CHANGE_APPROVAL_WORKFLOW)
    assert set(order) == {"start", "plan", "approval", "notify", "rejected", "end"}
    assert _canon_type("human-approval") == "approval"


async def _publish(client, headers, admin_headers, name: str, workflow: dict) -> str:
    r = await client.post(
        "/api/workflows",
        headers=headers,
        json={
            "name": name,
            "description": "approval 测试",
            "version": "1.0.0",
            "category": "能力编排",
            "tags": [],
            "workflow": workflow,
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=headers)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    return cap_id


@pytest.mark.asyncio
async def test_approval_pauses_then_resumes_on_approve(
    client, publisher_headers, admin_headers
):
    name = "approval-demo"
    cap_id = await _publish(client, publisher_headers, admin_headers, name, _approval_workflow())

    # 试运行：到达审批节点应暂停
    r = await client.post(
        f"/api/workflows/{cap_id}/test", headers=publisher_headers, json={"input": {}}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "waiting", body
    assert body["node_states"]["approval"] == "waiting"
    exec_id = body["id"]
    assert body["pending"] and body["pending"][0]["node_id"] == "approval"

    # 待审批列表（owner 可见）
    r = await client.get("/api/runtime/workflows/approvals", headers=publisher_headers)
    assert r.status_code == 200, r.text
    items = r.json()
    assert any(i["execution_id"] == exec_id and i["node_id"] == "approval" for i in items)

    # 通过 → 续跑
    r = await client.post(
        f"/api/runtime/workflows/executions/{exec_id}/approve",
        headers=publisher_headers,
        json={"node_id": "approval", "approved": True, "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    resumed = r.json()
    assert resumed["state"] == "succeeded", resumed
    assert resumed["node_states"]["done"] == "succeeded"
    assert resumed["outputs"]["approval"]["approved"] is True
    assert resumed["outputs"]["done"]["text"] == "done:True"
    assert not resumed["pending"]

    # 已结束的执行再次审批 → 409
    r = await client.post(
        f"/api/runtime/workflows/executions/{exec_id}/approve",
        headers=publisher_headers,
        json={"node_id": "approval", "approved": True},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_approval_reject_skips_approved_branch(
    client, publisher_headers, admin_headers
):
    name = "approval-demo-2"
    cap_id = await _publish(client, publisher_headers, admin_headers, name, _approval_workflow())
    r = await client.post(
        f"/api/workflows/{cap_id}/test", headers=publisher_headers, json={"input": {}}
    )
    exec_id = r.json()["id"]
    assert r.json()["state"] == "waiting"

    r = await client.post(
        f"/api/runtime/workflows/executions/{exec_id}/approve",
        headers=publisher_headers,
        json={"node_id": "approval", "approved": False, "comment": "no"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "succeeded", body
    assert body["node_states"]["done"] == "skipped"
    assert body["outputs"]["approval"]["approved"] is False


@pytest.mark.asyncio
async def test_approval_unknown_node_404(client, publisher_headers, admin_headers):
    name = "approval-demo-3"
    cap_id = await _publish(client, publisher_headers, admin_headers, name, _approval_workflow())
    r = await client.post(
        f"/api/workflows/{cap_id}/test", headers=publisher_headers, json={"input": {}}
    )
    exec_id = r.json()["id"]
    r = await client.post(
        f"/api/runtime/workflows/executions/{exec_id}/approve",
        headers=publisher_headers,
        json={"node_id": "nope", "approved": True},
    )
    assert r.status_code == 404
