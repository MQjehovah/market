"""Plugin 一键上传：拆出 agent/skill/mcp，发布后一键加入。"""

import io
import json
import zipfile

import pytest


def _plugin_zip(
    *,
    name: str = "工单助手",
    version: str = "1.0.0",
    with_skill: bool = True,
    with_mcp: bool = True,
) -> bytes:
    files: dict[str, bytes] = {
        "plugin.json": json.dumps(
            {
                "name": name,
                "description": "工单处理组合插件",
                "version": version,
                "primary_agent": "工单客服",
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        "agents/工单客服/agent.json": json.dumps(
            {"name": "工单客服", "description": "处理工单", "version": version},
            ensure_ascii=False,
        ).encode("utf-8"),
        "agents/工单客服/PROMPT.md": "# 你是工单客服\n".encode("utf-8"),
    }
    if with_skill:
        files["skills/工单分流/SKILL.md"] = "# 工单分流\n按优先级分类\n".encode("utf-8")
    if with_mcp:
        files["mcp.json"] = json.dumps(
            {
                "mcpServers": {
                    "jira-demo": {
                        "command": "python",
                        "args": ["-m", "demo"],
                        "description": "演示工单 MCP",
                    }
                }
            },
            ensure_ascii=False,
        ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_plugin_upload_materializes_components(client, publisher_headers, admin_headers):
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": "工单助手",
            "description": "组合插件",
            "type": "plugin",
            "version": "1.0.0",
            "category": "工单场景",
            "tags": ["工单"],
        },
    )
    assert r.status_code == 201, r.text
    plugin_id = r.json()["id"]

    r = await client.post(
        f"/api/publish/capabilities/{plugin_id}/artifact",
        headers=publisher_headers,
        files={"file": ("ticket.zip", _plugin_zip(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    schema = body["input_schema"]
    assert schema["kind"] == "plugin"
    comps = schema["components"]
    types = {c["type"] for c in comps}
    assert types == {"agent", "skill", "mcp"}
    assert any(c["role"] == "primary" and c["name"] == "工单客服" for c in comps)

    for c in comps:
        r = await client.get(f"/api/capabilities/{c['capability_id']}", headers=publisher_headers)
        assert r.status_code == 200
        assert r.json()["status"] == "draft"
        assert r.json()["type"] == c["type"]

    r = await client.post(
        f"/api/publish/capabilities/{plugin_id}/submit", headers=publisher_headers
    )
    assert r.status_code == 200
    r = await client.post(
        f"/api/admin/capabilities/{plugin_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"

    for c in comps:
        r = await client.get(f"/api/capabilities/{c['capability_id']}")
        assert r.status_code == 200
        assert r.json()["status"] == "published"


@pytest.mark.asyncio
async def test_join_plugin_adds_all_components(client, publisher_headers, admin_headers, user_headers):
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "一键插件", "type": "plugin", "version": "0.1.0", "description": "x"},
    )
    plugin_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{plugin_id}/artifact",
        headers=publisher_headers,
        files={"file": ("p.zip", _plugin_zip(name="一键插件", version="0.1.0"), "application/zip")},
    )
    assert r.status_code == 200, r.text
    comps = r.json()["input_schema"]["components"]
    await client.post(f"/api/publish/capabilities/{plugin_id}/submit", headers=publisher_headers)
    await client.post(
        f"/api/admin/capabilities/{plugin_id}/review",
        headers=admin_headers,
        json={"action": "approve"},
    )

    r = await client.post(
        "/api/my/capabilities",
        headers=user_headers,
        json={"capability_id": plugin_id},
    )
    assert r.status_code == 201, r.text
    assert "组件" in r.json()["message"]

    r = await client.get(
        "/api/my/capabilities?scope=added&include_components=true", headers=user_headers
    )
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert plugin_id in ids
    for c in comps:
        assert c["capability_id"] in ids

    # 默认列表隐藏 plugin-component，只保留 plugin 主记录
    r = await client.get("/api/my/capabilities?scope=added", headers=user_headers)
    ids_default = {c["id"] for c in r.json()}
    assert plugin_id in ids_default
    for c in comps:
        assert c["capability_id"] not in ids_default


@pytest.mark.asyncio
async def test_plugin_package_requires_plugin_json():
    from fastapi import HTTPException
    from app.services.packages import validate_package

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("skills/x/SKILL.md", b"# x\n")
    with pytest.raises(HTTPException) as exc:
        validate_package("plugin", buf.getvalue())
    assert exc.value.status_code == 422
    assert "plugin.json" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_wrapped_plugin_folder_accepted(client, publisher_headers):
    """整夹压缩 ticket-plugin/plugin.json 也应通过。"""
    inner = _plugin_zip(name="夹层插件", version="0.2.0", with_mcp=False)
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(inner)) as src, zipfile.ZipFile(buf, "w") as dst:
        for info in src.infolist():
            if info.is_dir():
                continue
            dst.writestr(f"ticket-plugin/{info.filename}", src.read(info.filename))
    wrapped = buf.getvalue()

    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "夹层插件", "type": "plugin", "version": "0.2.0"},
    )
    cap_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("w.zip", wrapped, "application/zip")},
    )
    assert r.status_code == 200, r.text
    types = {c["type"] for c in r.json()["input_schema"]["components"]}
    assert "agent" in types and "skill" in types
