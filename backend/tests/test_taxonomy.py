"""货架 / taxonomy / 默认浏览（控制面 IA）。"""

import io
import zipfile

import pytest


@pytest.mark.asyncio
async def test_taxonomy_endpoint(client):
    r = await client.get("/api/meta/taxonomy")
    assert r.status_code == 200
    body = r.json()
    assert "shelves" in body
    assert set(body["shelves"].keys()) == {"brick", "recipe", "install"}
    assert body["default_browse_kinds"] == ["agent", "skill", "mcp"]
    assert body["more_browse_kinds"] == ["workflow", "tool"]
    assert body["hidden_browse_kinds"] == ["plugin", "rule", "command", "hook"]
    assert "orchestration" in body
    assert body["orchestration"]["capability_dag"]["lands_in_agent_config"] is False
    assert "review_checklist" in body
    assert body["domain"]["note"]
    assert set(body["local_install_kinds"]) == {
        "agent",
        "skill",
        "mcp",
        "plugin",
        "rule",
        "command",
        "hook",
    }
    assert body["shelves"]["brick"]["kinds"] == ["skill", "mcp", "tool", "rule", "command", "hook"]
    assert len(body["ref_ways"]) == 3
    assert body["kinds"]["tool"]["local_install"] == "no"
    assert body["kinds"]["workflow"]["local_install"] == "no"
    assert body["shelves"]["brick"]["label"] == "组件"
    assert body["shelves"]["recipe"]["label"] == "助手"
    assert body["shelves"]["install"]["label"] == "安装包"


@pytest.mark.asyncio
async def test_package_template_download(client):
    for kind in ("skill", "mcp", "tool", "agent", "plugin", "rule", "command", "hook"):
        r = await client.get(f"/api/meta/package-templates/{kind}?name=demo")
        assert r.status_code == 200, kind
        assert "zip" in (r.headers.get("content-type") or "")
        with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
            names = zf.namelist()
            assert names, kind
    r_bad = await client.get("/api/meta/package-templates/workflow")
    assert r_bad.status_code == 404


@pytest.mark.asyncio
async def test_browse_default_includes_skill_excludes_more(client, publisher_headers, admin_headers):
    """默认浏览含 skill；mcp/workflow/tool 需显式筛选。"""
    for typ, name in (("skill", "tax-skill-demo"), ("agent", "tax-agent-demo")):
        r = await client.post(
            "/api/publish/capabilities",
            headers=publisher_headers,
            json={
                "name": name,
                "type": typ,
                "version": "0.1.0",
                "description": "taxonomy test",
                "visibility": "internal",
            },
        )
        assert r.status_code == 201, r.text

    r = await client.get("/api/capabilities")
    assert r.status_code == 200

    r_brick = await client.get("/api/capabilities?shelf=brick&include_bricks=true")
    assert r_brick.status_code == 200

    r_recipe = await client.get("/api/capabilities?shelf=recipe")
    assert r_recipe.status_code == 200
    for c in r_recipe.json()["items"]:
        assert c["type"] in ("agent", "workflow")

    r_install = await client.get("/api/capabilities?shelf=install")
    assert r_install.status_code == 200
    for c in r_install.json()["items"]:
        assert c["type"] == "plugin"


@pytest.mark.asyncio
async def test_install_policy_update(client, publisher_headers, admin_headers):
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": "policy-plugin-demo",
            "type": "plugin",
            "version": "0.1.0",
            "description": "install policy",
            "visibility": "internal",
            "install_policy": "optional",
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    assert r.json().get("install_policy") == "optional"

    r2 = await client.post(
        f"/api/capabilities/{cap_id}/install-policy",
        headers=publisher_headers,
        json={"install_policy": "required"},
    )
    assert r2.status_code == 200, r2.text
    assert r2.json()["install_policy"] == "required"
