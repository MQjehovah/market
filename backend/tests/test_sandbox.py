"""工具沙箱测试：AST 审计 / 真实执行 / 失败处理。"""

import io
import zipfile

import pytest


def _make_zip(tool_code: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("tool.json", '{"name": "沙箱测试工具", "version": "1.0.0"}')
        zf.writestr("schema.json", '{"input": {"a": "int"}, "output": {"sum": "int"}}')
        zf.writestr("implementation/tool.py", tool_code)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_audit_blocks_dangerous_code():
    from app.sandbox.executor import audit_source

    assert audit_source("import subprocess\n") != []
    assert audit_source("import os\nos.system('rm -rf /')\n") != []
    assert audit_source("eval('1+1')\n") != []
    assert audit_source("import math\ndef run(p): return {'v': math.sqrt(p['x'])}\n") == []


@pytest.mark.asyncio
async def test_tool_real_execution(client, publisher_headers, user_headers):
    code = (
        "import json\n"
        "def run(params):\n"
        "    return {'sum': params.get('a', 0) + params.get('b', 0), 'tool': '加法工具'}\n"
    )
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "沙箱加法工具", "description": "真实执行", "type": "tool", "version": "1.0.0"},
    )
    cap_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("add.zip", _make_zip(code), "application/zip")},
    )
    assert r.status_code == 200, r.text

    await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    admin = await _login_admin(client)
    await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin,
        json={"action": "approve"},
    )

    r = await client.post(
        "/api/runtime/tools/%E6%B2%99%E7%AE%B1%E5%8A%A0%E6%B3%95%E5%B7%A5%E5%85%B7/invoke",
        headers=user_headers,
        json={"params": {"a": 2, "b": 3}},
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["execution"] == "real"
    assert result["status"] == "ok"
    assert result["output"]["sum"] == 5


@pytest.mark.asyncio
async def test_tool_blocked_by_audit(client, publisher_headers, user_headers):
    code = "import subprocess\ndef run(params): return {'x': 1}\n"
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "危险工具", "description": "应被拦截", "type": "tool", "version": "1.0.0"},
    )
    cap_id = r.json()["id"]
    await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("evil.zip", _make_zip(code), "application/zip")},
    )
    await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    admin = await _login_admin(client)
    await client.post(f"/api/admin/capabilities/{cap_id}/review", headers=admin, json={"action": "approve"})

    r = await client.post(
        "/api/runtime/tools/%E5%8D%B1%E9%99%A9%E5%B7%A5%E5%85%B7/invoke",
        headers=user_headers,
        json={"params": {}},
    )
    assert r.status_code == 200, r.text
    result = r.json()["result"]
    assert result["execution"] == "real"
    assert result["status"] == "error"
    assert "禁止导入模块" in result["error"]


async def _login_admin(client):
    r = await client.post("/api/auth/login", json={"username": "admin", "password": "admin123"})
    return {"Authorization": f"Bearer {r.json()['access_token']}"}
