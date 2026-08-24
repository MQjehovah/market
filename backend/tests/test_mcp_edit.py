"""MCP 在线编辑：读取 connection、保存新版本草稿、附属文件保留。"""

import io
import json
import zipfile

import pytest


def _mcp_zip(name: str) -> bytes:
    files = {
        "mcp.json": json.dumps(
            {"name": name, "description": "测试 MCP", "version": "1.0.0", "category": "MCP"}
        ).encode("utf-8"),
        "connection.json": json.dumps(
            {
                "transport": "stdio",
                "command": "npx",
                "args": ["-y", "demo-mcp"],
                "env": {"API_KEY": ""},
            }
        ).encode("utf-8"),
        "tools.json": json.dumps([{"name": "ping", "description": "ping"}]).encode("utf-8"),
        "security.json": json.dumps({"allow": ["ping"]}).encode("utf-8"),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


async def _publish_mcp(client, publisher_headers, admin_headers, name: str) -> str:
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "description": "测试 MCP",
            "type": "mcp",
            "version": "1.0.0",
            "category": "MCP",
            "tags": ["测试"],
            "visibility": "internal",
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("mcp.zip", _mcp_zip(name), "application/zip")},
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
async def test_mcp_edit_read_and_save_new_version(client, publisher_headers, admin_headers):
    name = "editable-mcp"
    await _publish_mcp(client, publisher_headers, admin_headers, name)

    r = await client.get(f"/api/mcp/{name}/edit", headers=publisher_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability"]["version"] == "1.0.0"
    assert body["connection"]["command"] == "npx"
    assert any(f["path"] == "security.json" for f in body["files"])

    r = await client.put(
        f"/api/mcp/{name}/edit",
        headers=publisher_headers,
        json={
            "connection": {
                "transport": "http",
                "url": "https://example.com/mcp",
                "headers": {"Authorization": "Bearer x"},
            },
            "description": "更新后的 MCP",
            "tags": ["测试", "在线编辑"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability"]["version"] == "1.0.1"
    assert body["capability"]["status"] == "draft"
    assert body["connection"]["url"] == "https://example.com/mcp"
    assert body["capability"]["input_schema"]["transport"] == "http"
    assert any(f["path"] == "security.json" for f in body["files"])
    draft_id = body["capability"]["id"]

    r = await client.get(
        f"/api/publish/capabilities/{draft_id}/artifact/download",
        headers=publisher_headers,
    )
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
        assert "security.json" in names
        assert "tools.json" in names
        conn = json.loads(zf.read("connection.json").decode("utf-8"))
        assert conn["transport"] == "http"
        assert conn["url"] == "https://example.com/mcp"

    r = await client.post(f"/api/publish/capabilities/{draft_id}/submit", headers=publisher_headers)
    assert r.status_code == 200
    r = await client.post(
        f"/api/admin/capabilities/{draft_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"


@pytest.mark.asyncio
async def test_mcp_edit_requires_owner(client, publisher_headers, admin_headers, user_headers):
    name = "owner-mcp"
    await _publish_mcp(client, publisher_headers, admin_headers, name)

    r = await client.put(
        f"/api/mcp/{name}/edit",
        headers=user_headers,
        json={"connection": {"transport": "stdio", "command": "echo"}},
    )
    assert r.status_code == 403
