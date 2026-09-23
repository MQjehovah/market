"""线上网关：skill 返回 SKILL.md、消费投影、taxonomy 入口。"""

import io
import json
import zipfile
from unittest.mock import patch

import pytest

from app.services.dashboard_consume import dashboard_projection
from marketplace_mcp import server as mcp_bridge
from test_workflow import _publish_capability


def _skill_zip(name: str, body: str = "步骤一：写测试\n") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.json",
            json.dumps({"name": name, "description": "d", "version": "1.0.0"}).encode("utf-8"),
        )
        zf.writestr("SKILL.md", f"# {name}\n\n{body}".encode("utf-8"))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_activate_skill_returns_skill_md(client, publisher_headers, admin_headers):
    name = "线上技能正文"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "skill", _skill_zip(name, "按需注入上下文。")
    )
    r = await client.post(
        f"/api/runtime/skills/{name}/activate",
        headers=admin_headers,
        json={"context": "写单测"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    result = body["result"]
    assert result["activated"] is True
    assert result["context"] == "写单测"
    assert "按需注入上下文" in result["skill_md"]
    assert name in result["skill_md"]
    assert "SKILL.md" in (result.get("note") or "")


@pytest.mark.asyncio
async def test_taxonomy_includes_online_gateway(client):
    r = await client.get("/api/meta/taxonomy")
    assert r.status_code == 200
    ways = r.json()["consume_ways"]
    ids = [w["id"] for w in ways]
    assert "online_gateway" in ids
    assert "cap_gateway" in ids
    online = next(w for w in ways if w["id"] == "online_gateway")
    assert "marketplace_mcp" in online["api"]


def test_dashboard_projection_tool_and_skill_online_fields():
    class ToolCap:
        type = "tool"
        name = "哈希工具"
        input_schema = {"schema": {"type": "object"}}

    tool = dashboard_projection(ToolCap())
    assert tool["mode"] == "remote-tool"
    assert "/api/runtime/tools/" in tool["invoke"]
    assert tool["invoke"].endswith("/invoke")
    assert "不下载" in tool.get("note", "")

    class SkillCap:
        type = "skill"
        name = "TDD"
        input_schema = {}

    skill = dashboard_projection(SkillCap())
    assert skill["mode"] == "local-skill"
    assert skill["online"] == "/api/runtime/skills/TDD/activate"
    assert "SKILL.md" in skill.get("note", "")

    class McpCap:
        type = "mcp"
        name = "pkg-mcp"
        input_schema = {
            "transport": "stdio",
            "command": "python",
            "args": ["implementation/server.py"],
            "tools": [{"name": "ping", "description": "p"}],
        }

    mcp = dashboard_projection(McpCap())
    assert mcp["mode"] == "gateway-sse"
    assert mcp["sse_url"] == "/api/mcp-gateway/cap/pkg-mcp/sse"
    assert "Bearer" in str(mcp["mcp"].get("headers") or {})


def test_marketplace_mcp_activate_skill_surfaces_md():
    fake = {
        "capability": {"name": "TDD 开发工作流"},
        "result": {
            "skill": "TDD 开发工作流",
            "skill_md": "# TDD\n\n先写测试。",
            "context": "写测试",
            "note": "ok",
        },
    }
    with patch.object(mcp_bridge, "_api", return_value=fake):
        out = mcp_bridge._handle_tool(
            "marketplace_activate_skill",
            {"name": "TDD 开发工作流", "context": "写测试"},
        )
    text = out["content"][0]["text"]
    assert "先写测试" in text
    assert "TDD" in text


def test_marketplace_mcp_call_mcp_forwards():
    fake = {"mcp": "doc-mcp", "tool": "echo", "result": {"ok": True}}
    with patch.object(mcp_bridge, "_api", return_value=fake) as api:
        out = mcp_bridge._handle_tool(
            "marketplace_call_mcp",
            {"name": "doc-mcp", "tool": "echo", "params": {"x": 1}},
        )
    assert api.called
    args = api.call_args
    assert args[0][0] == "POST"
    assert "/api/runtime/mcp/" in args[0][1]
    assert "call" in args[0][1]
    assert json.loads(out["content"][0]["text"])["tool"] == "echo"


def test_marketplace_mcp_tools_include_call_mcp():
    names = {t["name"] for t in mcp_bridge.TOOLS}
    assert "marketplace_call_mcp" in names
    assert "marketplace_use_tool" in names
    assert "marketplace_activate_skill" in names
