"""工作流触发器 + code 沙箱单测。"""
from datetime import datetime

import pytest

from app.services.sandbox import run_code
from app.services.workflow_triggers import cron_due, gitlab_trigger_input


@pytest.mark.asyncio
async def test_run_code_python_main():
    res = await run_code("python", "def main(a):\n    return {'doubled': a * 2}\n", {"a": 4})
    assert res["result"] == {"doubled": 8}


@pytest.mark.asyncio
async def test_run_code_result_var_and_failure():
    res = await run_code("python", "result = 1 + 2\n", {})
    assert res["result"] == 3
    with pytest.raises(Exception):
        await run_code("python", "raise ValueError('boom')\n", {})
    with pytest.raises(Exception):
        await run_code("node", "console.log(1)", {})


def test_cron_due():
    now = datetime(2026, 1, 1, 8, 30)
    assert cron_due("30 8 * * *", now) is True
    assert cron_due("*/15 8 * * *", now) is True
    assert cron_due("0 8 * * *", now) is False
    assert cron_due("30 0 * * *", now) is False
    assert cron_due("bad expr", now) is False


@pytest.mark.asyncio
async def test_gitlab_trigger_input_mapping_and_token():
    trig = {"type": "gitlab", "token": "sec-token"}
    mr = {
        "object_kind": "merge_request",
        "project": {"id": 7, "path_with_namespace": "demo/api"},
        "user": {"name": "张三"},
        "object_attributes": {
            "iid": 12, "title": "add feature", "source_branch": "feat",
            "target_branch": "main", "action": "open", "url": "http://g/mr/12",
        },
    }
    with pytest.raises(Exception):
        await gitlab_trigger_input(None, trig, {"x-gitlab-token": "wrong"}, mr)
    out = await gitlab_trigger_input(None, trig, {"x-gitlab-token": "sec-token"}, mr)
    assert out["project"] == "demo/api"
    assert out["mr_iid"] == "12"
    assert out["title"] == "add feature"
    assert out["diff"] == ""  # 未配置 gitlab 凭据时回退空

    push = {
        "object_kind": "push",
        "project": {"id": 7, "path_with_namespace": "demo/api"},
        "user_name": "李四",
        "ref": "refs/heads/main",
        "before": "0" * 40, "after": "a" * 40,
        "commits": [{"id": "a" * 40, "message": "fix bug"}],
    }
    out = await gitlab_trigger_input(None, trig, {"x-gitlab-token": "sec-token"}, push)
    assert out["event"] == "push"
    assert out["commit"] == "a" * 40
    assert out["commit_message"] == "fix bug"


def _gitlab_workflow() -> dict:
    return {
        "name": "gl-demo",
        "trigger": {"type": "gitlab", "token": "sec-token"},
        "nodes": [
            {"id": "start", "type": "start", "params": {"fields": ["project", "mr_iid", "diff"]}},
            {"id": "echo", "type": "template", "params": {"template": "${start.project}!${start.mr_iid}:${start.diff}"}},
            {"id": "end", "type": "end", "params": {"outputs": {"result": "${echo.text}"}}},
        ],
        "edges": [{"from": "start", "to": "echo"}, {"from": "echo", "to": "end"}],
    }


async def _publish(client, headers, admin_headers, name, workflow):
    r = await client.post(
        "/api/workflows", headers=headers,
        json={"name": name, "version": "1.0.0", "category": "能力编排", "workflow": workflow},
    )
    assert r.status_code == 201, r.text
    cid = r.json()["id"]
    assert (await client.post(f"/api/publish/capabilities/{cid}/submit", headers=headers)).status_code == 200
    r = await client.post(
        f"/api/admin/capabilities/{cid}/review", headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_gitlab_trigger_route(client, publisher_headers, admin_headers):
    name = "gl-demo"
    await _publish(client, publisher_headers, admin_headers, name, _gitlab_workflow())
    payload = {
        "object_kind": "merge_request",
        "project": {"id": 7, "path_with_namespace": "demo/api"},
        "object_attributes": {"iid": 12, "title": "t", "action": "open"},
    }
    # 错误 token → 401
    r = await client.post(
        f"/api/runtime/workflows/{name}/trigger",
        headers={"X-Gitlab-Token": "wrong"}, json=payload,
    )
    assert r.status_code == 401
    # 正确 token → 触发并映射入参
    r = await client.post(
        f"/api/runtime/workflows/{name}/trigger",
        headers={"X-Gitlab-Token": "sec-token"}, json=payload,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["state"] == "succeeded", body
    assert body["outputs"]["end"]["result"] == "demo/api!12:"
