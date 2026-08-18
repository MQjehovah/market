"""草稿可改类型 / 撤回审核 / 删除待审，避免名称被占死。"""

import io
import json
import zipfile

import pytest


def _tool_zip(name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("tool.json", json.dumps({"name": name, "version": "0.1.0"}))
        zf.writestr("schema.json", '{"type": "object", "properties": {}}')
        zf.writestr("implementation/tool.py", "def run(params):\n    return {}\n")
    return buf.getvalue()


async def _create(client, headers, name: str, type_: str = "agent") -> str:
    r = await client.post(
        "/api/publish/capabilities",
        headers=headers,
        json={"name": name, "description": "x", "type": type_, "version": "0.1.0"},
    )
    assert r.status_code == 201, r.text
    return r.json()["id"]


@pytest.mark.asyncio
async def test_draft_can_change_type_and_recreate_after_delete(client, publisher_headers):
    name = "改类型工具"
    cap_id = await _create(client, publisher_headers, name, "agent")

    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", _tool_zip(name), "application/zip")},
    )
    assert r.status_code == 422

    r = await client.put(
        f"/api/publish/capabilities/{cap_id}",
        headers=publisher_headers,
        json={"type": "tool"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["type"] == "tool"
    assert r.json()["artifacts"] == []

    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", _tool_zip(name), "application/zip")},
    )
    assert r.status_code == 200, r.text

    r = await client.delete(f"/api/publish/capabilities/{cap_id}", headers=publisher_headers)
    assert r.status_code == 200, r.text

    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": name, "description": "again", "type": "tool", "version": "0.1.0"},
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_reviewing_can_withdraw_or_delete(client, publisher_headers):
    name = "待审可撤回"
    cap_id = await _create(client, publisher_headers, name, "skill")
    r = await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    assert r.status_code == 200
    assert r.json()["status"] == "reviewing"

    r = await client.put(
        f"/api/publish/capabilities/{cap_id}",
        headers=publisher_headers,
        json={"type": "tool"},
    )
    assert r.status_code == 409

    r = await client.post(f"/api/publish/capabilities/{cap_id}/withdraw", headers=publisher_headers)
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "draft"

    r = await client.put(
        f"/api/publish/capabilities/{cap_id}",
        headers=publisher_headers,
        json={"type": "tool"},
    )
    assert r.status_code == 200
    assert r.json()["type"] == "tool"

    r = await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    assert r.status_code == 200
    r = await client.delete(f"/api/publish/capabilities/{cap_id}", headers=publisher_headers)
    assert r.status_code == 200, r.text

    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": name, "type": "tool", "version": "0.1.0"},
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_cannot_change_type_when_other_version_exists(client, publisher_headers, admin_headers):
    name = "多版本锁类型"
    cap_id = await _create(client, publisher_headers, name, "tool")
    await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve"},
    )
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/versions",
        headers=publisher_headers,
        json={"new_version": "0.1.1"},
    )
    assert r.status_code == 201
    draft_id = r.json()["id"]
    r = await client.put(
        f"/api/publish/capabilities/{draft_id}",
        headers=publisher_headers,
        json={"type": "agent"},
    )
    assert r.status_code == 409
