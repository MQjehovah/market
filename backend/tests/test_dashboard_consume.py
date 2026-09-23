"""桌面消费投影与 download ?type= 消歧。"""

import io
import json
import zipfile

import pytest

from app.services.dashboard_consume import classify_dashboard_mcp, dashboard_projection
from test_workflow import _publish_capability, _tool_zip


def _skill_zip(name: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.json",
            json.dumps({"name": name, "description": "d", "version": "1.0.0"}).encode("utf-8"),
        )
        zf.writestr("SKILL.md", f"# {name}\n\nbody\n".encode("utf-8"))
    return buf.getvalue()


def test_classify_dashboard_mcp_local_vs_gateway():
    assert (
        classify_dashboard_mcp({"transport": "stdio", "command": "npx", "args": ["-y", "foo"]})
        == "local-stdio"
    )
    assert (
        classify_dashboard_mcp(
            {
                "transport": "stdio",
                "command": "python",
                "args": ["implementation/server.py"],
            }
        )
        == "gateway-sse"
    )
    assert (
        classify_dashboard_mcp({"transport": "sse", "url": "https://example/sse"}) == "local-sse"
    )
    assert (
        classify_dashboard_mcp(
            {"transport": "sse", "url": "https://example/sse", "headers": {"Authorization": "x"}}
        )
        == "gateway-sse"
    )
    assert classify_dashboard_mcp({"transport": "gateway", "server": "x"}) == "gateway-sse"


def test_dashboard_projection_mcp_urls():
    class Cap:
        type = "mcp"
        name = "doc-mcp"
        input_schema = {
            "transport": "stdio",
            "command": "python",
            "args": ["implementation/server.py"],
            "tools": [{"name": "echo", "description": "回显"}],
        }

    proj = dashboard_projection(Cap())
    assert proj["mode"] == "gateway-sse"
    assert proj["sse_url"] == "/api/mcp-gateway/cap/doc-mcp/sse"
    assert proj["mcp"]["url"] == proj["sse_url"]
    assert proj["tools"] == [{"name": "echo", "description": "回显"}]


@pytest.mark.asyncio
async def test_browse_and_detail_expose_consumers_dashboard(client):
    r = await client.get("/api/capabilities?include_bricks=true&type=mcp")
    assert r.status_code == 200
    items = r.json()["items"]
    mcp = next((c for c in items if c["type"] == "mcp"), None)
    assert mcp is not None
    dash = mcp["consumers"]["dashboard"]
    assert dash["mode"] in ("local-stdio", "local-sse", "gateway-sse")
    assert dash["sse_url"].startswith("/api/mcp-gateway/cap/")

    r = await client.get(f"/api/capabilities/{mcp['id']}")
    assert r.status_code == 200
    assert r.json()["consumers"]["dashboard"]["sse_url"] == dash["sse_url"]


@pytest.mark.asyncio
async def test_download_type_disambiguates_same_name(client, publisher_headers, admin_headers):
    name = "同名消歧"
    skill_id = await _publish_capability(
        client, publisher_headers, admin_headers, name, "skill", _skill_zip(name)
    )
    # 同名不同 version：name+version 全局唯一
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "description": "工具侧",
            "type": "tool",
            "version": "1.1.0",
            "visibility": "internal",
        },
    )
    assert r.status_code == 201, r.text
    tool_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{tool_id}/artifact",
        headers=publisher_headers,
        files={"file": ("t.zip", _tool_zip(name), "application/zip")},
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/publish/capabilities/{tool_id}/submit", headers=publisher_headers)
    assert r.status_code == 200
    r = await client.post(
        f"/api/admin/capabilities/{tool_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/capabilities/{name}/download?type=skill")
    assert r.status_code == 200, r.text
    assert r.headers["x-capability-type"] == "skill"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "SKILL.md" in zf.namelist()

    r = await client.get(f"/api/capabilities/{name}/download?type=tool")
    assert r.status_code == 200, r.text
    assert r.headers["x-capability-type"] == "tool"
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "schema.json" in zf.namelist()

    # 不带 type：按 semver 取高版本 → tool 1.1.0
    r = await client.get(f"/api/capabilities/{name}/download")
    assert r.status_code == 200
    assert r.headers["x-capability-type"] == "tool"

    r = await client.get(f"/api/capabilities/{name}/download?type=mcp")
    assert r.status_code == 404
    r = await client.get(f"/api/capabilities/{name}/download?type=not-a-kind")
    assert r.status_code == 422
    assert skill_id


@pytest.mark.asyncio
async def test_plugin_detail_lifts_components(client, publisher_headers, admin_headers):
    from test_plugin import _plugin_zip

    name = "投影安装包"
    cap_id = await _publish_capability(
        client, publisher_headers, admin_headers, name, "plugin", _plugin_zip(name=name)
    )
    r = await client.get(f"/api/capabilities/{cap_id}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["consumers"]["dashboard"]["mode"] == "plugin"
    types = {c["type"] for c in body["components"]}
    assert "agent" in types
    assert any(c.get("name") for c in body["consumers"]["dashboard"]["components"])
