"""Agent embedded skills/MCP extraction + browse filters."""

import io
import json
import zipfile

import pytest


def _agent_zip_with_embedded(*, name: str = "demo-agent") -> bytes:
    files = {
        "agent.json": json.dumps(
            {
                "name": name,
                "description": "demo",
                "version": "1.0.0",
                "skills": [
                    {
                        "name": "wecom-message",
                        "display_name": "WeCom",
                        "description": "msg",
                        "version": "1.0.0",
                    }
                ],
                "mcp_servers": [
                    {
                        "name": "wecom-api",
                        "command": "npx",
                        "args": ["-y", "@demo/wecom"],
                        "env": {"WECOM_CORPID": "${CORPID}"},
                    }
                ],
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        "PROMPT.md": b"# demo\n",
        "skills/text-summarizer/SKILL.md": b"# summarize\n",
        "skills/text-summarizer/skill.json": json.dumps(
            {
                "identity": {
                    "name": "text-summarizer",
                    "version": "2.0.0",
                    "display_name": "Summarizer",
                    "description": "sum",
                }
            },
            ensure_ascii=False,
        ).encode("utf-8"),
        "skills/legacy.yaml": b"pipeline: []\n",
        "mcp/config.json": json.dumps(
            {
                "mcpServers": {
                    "aliyun-log": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "@demo/log"],
                        "env": {"CRED": "${CRED}"},
                    }
                }
            }
        ).encode("utf-8"),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path, data in files.items():
            zf.writestr(path, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_agent_upload_extracts_embedded_skills_mcp(client, publisher_headers):
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": "demo-agent",
            "description": "with embedded",
            "type": "agent",
            "version": "1.0.0",
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]

    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("a.zip", _agent_zip_with_embedded(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    schema = r.json()["input_schema"]
    skill_names = {s["name"] for s in schema["embedded_skills"]}
    assert "wecom-message" in skill_names
    assert "text-summarizer" in skill_names
    assert "legacy" in skill_names
    mcp_names = {m["name"] for m in schema["embedded_mcp"]}
    assert "wecom-api" in mcp_names
    assert "aliyun-log" in mcp_names


@pytest.mark.asyncio
async def test_browse_filter_by_skill_and_hide_plugin_components(
    client, publisher_headers, admin_headers
):
    def _unique_plugin_zip() -> bytes:
        files = {
            "plugin.json": json.dumps(
                {
                    "name": "filter-plugin",
                    "description": "p",
                    "version": "1.0.0",
                    "primary_agent": "filter-agent-x",
                },
                ensure_ascii=False,
            ).encode("utf-8"),
            "agents/filter-agent-x/agent.json": json.dumps(
                {"name": "filter-agent-x", "description": "a", "version": "1.0.0"},
                ensure_ascii=False,
            ).encode("utf-8"),
            "agents/filter-agent-x/PROMPT.md": b"# a\n",
            "skills/filter-skill-x/SKILL.md": b"# s\n",
            "mcp.json": json.dumps(
                {"mcpServers": {"filter-mcp-x": {"command": "python", "args": ["-m", "x"]}}}
            ).encode("utf-8"),
        }
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as zf:
            for path, data in files.items():
                zf.writestr(path, data)
        return buf.getvalue()

    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": "filter-plugin",
            "description": "p",
            "type": "plugin",
            "version": "1.0.0",
            "category": "ops",
        },
    )
    plugin_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{plugin_id}/artifact",
        headers=publisher_headers,
        files={"file": ("p.zip", _unique_plugin_zip(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    comps = r.json()["input_schema"]["components"]
    skill_comp = next(c for c in comps if c["type"] == "skill")
    await client.post(f"/api/publish/capabilities/{plugin_id}/submit", headers=publisher_headers)
    await client.post(
        f"/api/admin/capabilities/{plugin_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )

    # 默认浏览隐藏全部 plugin-component
    r = await client.get("/api/capabilities?type=skill&page_size=100")
    assert r.status_code == 200
    names = {i["name"] for i in r.json()["items"]}
    assert "filter-skill-x" not in names

    # include_components=true 时可列出
    r = await client.get("/api/capabilities?type=skill&include_components=true&page_size=100")
    assert r.status_code == 200
    names = {i["name"] for i in r.json()["items"]}
    assert "filter-skill-x" in names
    skill_item = next(i for i in r.json()["items"] if i["name"] == "filter-skill-x")
    assert "plugin-component" in (skill_item.get("tags") or [])

    r = await client.get("/api/capabilities?skill=filter-skill-x&page_size=100")
    assert r.status_code == 200
    assert any(i["name"] == "filter-plugin" for i in r.json()["items"])

    # 子能力详情有 parent_plugin_id
    r = await client.get(f"/api/capabilities/{skill_comp['capability_id']}")
    assert r.status_code == 200
    assert r.json().get("parent_plugin_id") == plugin_id

    # sync 目录仍包含已发布组件，便于消费者依赖
    r = await client.get("/api/capabilities/sync")
    sync_names = {(i["name"], i["type"]) for i in r.json()}
    assert ("filter-skill-x", "skill") in sync_names
    assert ("filter-mcp-x", "mcp") in sync_names


@pytest.mark.asyncio
async def test_agent_detail_links_published_skill(client, publisher_headers, admin_headers):
    # publish a standalone skill that matches embedded name
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "wecom-message", "type": "skill", "version": "1.0.0", "description": "s"},
    )
    skill_id = r.json()["id"]
    skill_zip = io.BytesIO()
    with zipfile.ZipFile(skill_zip, "w") as zf:
        zf.writestr(
            "skill.json",
            json.dumps({"name": "wecom-message", "version": "1.0.0"}).encode("utf-8"),
        )
        zf.writestr("SKILL.md", b"# wecom\n")
    r = await client.post(
        f"/api/publish/capabilities/{skill_id}/artifact",
        headers=publisher_headers,
        files={"file": ("s.zip", skill_zip.getvalue(), "application/zip")},
    )
    assert r.status_code == 200, r.text
    await client.post(f"/api/publish/capabilities/{skill_id}/submit", headers=publisher_headers)
    await client.post(
        f"/api/admin/capabilities/{skill_id}/review",
        headers=admin_headers,
        json={"action": "approve"},
    )

    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "link-agent", "type": "agent", "version": "1.0.0", "description": "a"},
    )
    agent_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{agent_id}/artifact",
        headers=publisher_headers,
        files={"file": ("a.zip", _agent_zip_with_embedded(name="link-agent"), "application/zip")},
    )
    assert r.status_code == 200, r.text
    # 上传时即固化市场关联
    upload_skills = r.json()["input_schema"]["embedded_skills"]
    wecom_upload = next(s for s in upload_skills if s["name"] == "wecom-message")
    assert wecom_upload.get("capability_id") == skill_id
    assert wecom_upload.get("market_version") == "1.0.0"

    await client.post(f"/api/publish/capabilities/{agent_id}/submit", headers=publisher_headers)
    await client.post(
        f"/api/admin/capabilities/{agent_id}/review",
        headers=admin_headers,
        json={"action": "approve"},
    )

    r = await client.get(f"/api/capabilities/{agent_id}", headers=publisher_headers)
    assert r.status_code == 200
    skills = r.json()["input_schema"]["embedded_skills"]
    wecom = next(s for s in skills if s["name"] == "wecom-message")
    assert wecom.get("capability_id") == skill_id

    # skill 详情 used_by 包含该 agent
    r = await client.get(f"/api/capabilities/{skill_id}")
    assert r.status_code == 200
    used = r.json().get("used_by") or []
    assert any(u["capability_id"] == agent_id and u["type"] == "agent" for u in used)
