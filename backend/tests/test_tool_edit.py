"""工具在线编辑：读取 schema、保存新版本草稿、附属文件保留。"""

import io
import json
import zipfile

import pytest


def _tool_zip(name: str, schema: dict | None = None) -> bytes:
    files = {
        "tool.json": json.dumps(
            {"name": name, "description": "测试工具", "version": "1.0.0", "category": "工具"}
        ).encode("utf-8"),
        "schema.json": json.dumps(
            schema
            or {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }
        ).encode("utf-8"),
        "implementation/tool.py": b"def run(params):\n    return {'ok': True}\n",
        "docs/readme.md": b"tool docs",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


async def _publish_tool(client, publisher_headers, admin_headers, name: str) -> str:
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "description": "测试工具",
            "type": "tool",
            "version": "1.0.0",
            "category": "工具",
            "tags": ["测试"],
            "visibility": "internal",
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("tool.zip", _tool_zip(name), "application/zip")},
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
async def test_tool_edit_read_and_save_new_version(client, publisher_headers, admin_headers):
    name = "editable-tool"
    await _publish_tool(client, publisher_headers, admin_headers, name)

    r = await client.get(f"/api/tools/{name}/edit", headers=publisher_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability"]["version"] == "1.0.0"
    assert body["tool_schema"]["properties"]["text"]["type"] == "string"
    assert "def run" in body["implementation"]
    assert any(f["path"] == "docs/readme.md" for f in body["files"])

    r = await client.put(
        f"/api/tools/{name}/edit",
        headers=publisher_headers,
        json={
            "tool_schema": {
                "type": "object",
                "properties": {"q": {"type": "string"}},
                "required": ["q"],
            },
            "implementation": "def run(params):\n    return {'q': params.get('q')}\n",
            "description": "更新后的工具",
            "tags": ["测试", "在线编辑"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability"]["version"] == "1.0.1"
    assert body["capability"]["status"] == "draft"
    assert body["tool_schema"]["required"] == ["q"]
    assert "params.get('q')" in body["implementation"]
    assert any(f["path"] == "docs/readme.md" for f in body["files"])
    draft_id = body["capability"]["id"]

    r = await client.get(
        f"/api/publish/capabilities/{draft_id}/artifact/download",
        headers=publisher_headers,
    )
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
        assert "docs/readme.md" in names
        schema = json.loads(zf.read("schema.json").decode("utf-8"))
        assert schema["required"] == ["q"]
        assert "params.get('q')" in zf.read("implementation/tool.py").decode("utf-8")

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
async def test_tool_edit_requires_owner(client, publisher_headers, admin_headers, user_headers):
    name = "owner-tool-edit-guard"
    await _publish_tool(client, publisher_headers, admin_headers, name)

    r = await client.put(
        f"/api/tools/{name}/edit",
        headers=user_headers,
        json={"tool_schema": {"type": "object", "properties": {}}, "implementation": "def run(p):\n    return {}\n"},
    )
    assert r.status_code == 403
