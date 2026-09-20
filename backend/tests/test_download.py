"""能力层消费者接口测试：同步目录 + 按名称/版本下载能力包。"""

import io
import json
import zipfile
from urllib.parse import quote

import pytest


def _tool_package(name: str) -> bytes:
    files = {
        "tool.json": json.dumps(
            {"name": name, "description": "下载测试工具", "version": "1.0.0"}
        ).encode("utf-8"),
        "schema.json": json.dumps(
            {
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            }
        ).encode("utf-8"),
        "implementation/tool.py": "class Tool:\n    pass\n".encode("utf-8"),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_sync_catalog_lists_latest_published(client):
    r = await client.get("/api/capabilities/sync")
    assert r.status_code == 200
    items = r.json()
    assert len(items) >= 4
    types = {i["type"] for i in items}
    assert {"agent", "tool", "skill", "mcp"}.issubset(types)
    assert all(i["status"] in ("published", "deprecated") for i in items)
    names = [i["name"] for i in items]
    assert len(names) == len(set(names))
    assert all(i["download_url"].startswith("/api/capabilities/") for i in items)


@pytest.mark.asyncio
async def test_consumer_can_download_published_package(client, publisher_headers, admin_headers):
    name = "下载测试工具"
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "description": "测试下载",
            "type": "tool",
            "version": "1.0.0",
            "category": "测试",
            "tags": ["下载"],
            "visibility": "internal",
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]

    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("tool.zip", _tool_package(name), "application/zip")},
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

    r = await client.get("/api/capabilities/sync")
    item = next(i for i in r.json() if i["name"] == name)
    assert item["has_artifact"] is True

    # 详情响应必须包含 artifacts，前端详情页依赖该字段
    r = await client.get(f"/api/capabilities/{cap_id}")
    assert r.status_code == 200
    assert "artifacts" in r.json()
    assert len(r.json()["artifacts"]) == 1

    r = await client.get(item["download_url"])
    assert r.status_code == 200, r.text
    assert r.headers["content-type"].startswith("application/zip")
    assert r.headers["x-capability-name"] == quote(name, safe="")
    assert r.headers["x-capability-type"] == "tool"
    assert r.headers["x-capability-version"] == "1.0.0"
    assert r.headers["x-capability-checksum"]

    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
    assert {"tool.json", "schema.json", "implementation/tool.py"} <= names


@pytest.mark.asyncio
async def test_download_unknown_or_without_artifact_returns_404(client):
    r = await client.get("/api/capabilities/不存在的能力/download")
    assert r.status_code == 404

    # 演示数据里的能力已发布但没有上传能力包
    r = await client.get("/api/capabilities/sync")
    item = next(i for i in r.json() if not i["has_artifact"])
    r = await client.get(item["download_url"])
    assert r.status_code == 404
