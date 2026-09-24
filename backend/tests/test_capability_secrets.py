"""能力级平台密钥：加密存库、API 权限、平台轨注入（全用户共用、按名跨版本）。"""

import io
import json
import sys
import zipfile

import pytest

from test_workflow import _publish_capability


def _mcp_zip(name: str, env: dict[str, str] | None = None) -> bytes:
    """构造带 connection.json env 声明的 mcp 能力包（不需要真实可运行）。"""
    files = {
        "mcp.json": json.dumps({"name": name, "description": "平台密钥测试"}).encode("utf-8"),
        "connection.json": json.dumps(
            {
                "transport": "stdio",
                "command": sys.executable,
                "args": ["implementation/server.py"],
                "env": env or {},
            }
        ).encode("utf-8"),
        "tools.json": json.dumps({"tools": []}).encode("utf-8"),
        "security.json": json.dumps({"sandbox": False}).encode("utf-8"),
        "implementation/server.py": b"# stub\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


def _scope(auth: str) -> dict:
    """能力级 relay 的最小 ASGI scope。"""
    return {
        "type": "http",
        "asgi": {"version": "3.0"},
        "method": "GET",
        "scheme": "http",
        "path": "/api/mcp-gateway/relay/x/sse",
        "raw_path": b"/api/mcp-gateway/relay/x/sse",
        "root_path": "",
        "query_string": b"",
        "headers": [(b"authorization", auth.encode("latin-1"))],
        "client": ("127.0.0.1", 1),
        "server": ("test", 80),
    }


async def _set_platform_secret(client, headers, cap_id: str, key: str, value: str):
    r = await client.put(
        f"/api/capabilities/{cap_id}/platform-secrets",
        headers=headers,
        json={"secrets": {key: value}},
    )
    assert r.status_code == 200, r.text
    return r.json()


# ---------- API：CRUD / 权限 / 校验 / declared_env ----------


@pytest.mark.asyncio
async def test_platform_secrets_crud_permissions_and_declared_env(
    client, publisher_headers, admin_headers, user_headers
):
    name = "平台密钥能力"
    cap_id = await _publish_capability(
        client,
        publisher_headers,
        admin_headers,
        name,
        "mcp",
        _mcp_zip(name, {"DINGTALK_APP_SECRET": "${DINGTALK_APP_SECRET}"}),
    )

    # 作者可读；declared_env 来自 input_schema（包内 env 声明）
    r = await client.get(f"/api/capabilities/{cap_id}/platform-secrets", headers=publisher_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["items"] == []
    assert body["declared_env"] == ["DINGTALK_APP_SECRET"]

    # 非作者普通用户：不可读/写
    r = await client.get(f"/api/capabilities/{cap_id}/platform-secrets", headers=user_headers)
    assert r.status_code == 403
    r = await client.put(
        f"/api/capabilities/{cap_id}/platform-secrets",
        headers=user_headers,
        json={"secrets": {"DINGTALK_APP_SECRET": "x"}},
    )
    assert r.status_code == 403

    # 作者批量写入：空值跳过，不回显明文
    r = await client.put(
        f"/api/capabilities/{cap_id}/platform-secrets",
        headers=publisher_headers,
        json={"secrets": {"DINGTALK_APP_SECRET": "s3cret", "EMPTY_KEY": ""}},
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [i["key_name"] for i in items] == ["DINGTALK_APP_SECRET"]
    assert items[0]["updated_by"]
    assert "s3cret" not in r.text

    # 非法键名 422
    r = await client.put(
        f"/api/capabilities/{cap_id}/platform-secrets",
        headers=publisher_headers,
        json={"secrets": {"1BAD": "x"}},
    )
    assert r.status_code == 422

    # admin 可管理（新增另一键）
    await _set_platform_secret(client, admin_headers, cap_id, "EXTRA_KEY", "v2")
    r = await client.get(f"/api/capabilities/{cap_id}/platform-secrets", headers=admin_headers)
    assert {i["key_name"] for i in r.json()["items"]} == {"DINGTALK_APP_SECRET", "EXTRA_KEY"}

    # 删除：作者可删，重复删 404
    r = await client.delete(
        f"/api/capabilities/{cap_id}/platform-secrets/EXTRA_KEY", headers=publisher_headers
    )
    assert r.status_code == 200
    r = await client.delete(
        f"/api/capabilities/{cap_id}/platform-secrets/EXTRA_KEY", headers=publisher_headers
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_platform_track_injects_platform_env_for_all_users(
    client, publisher_headers, admin_headers, user_headers
):
    """relay 平台轨：admin 与普通用户得到同一份平台密钥；个人同名密钥不影响。"""
    from app.services.mcp_gateway import authorize_capability_gateway

    name = "平台注入能力"
    cap_id = await _publish_capability(
        client,
        publisher_headers,
        admin_headers,
        name,
        "mcp",
        _mcp_zip(name, {"DINGTALK_APP_SECRET": "${DINGTALK_APP_SECRET}"}),
    )
    await _set_platform_secret(client, admin_headers, cap_id, "DINGTALK_APP_SECRET", "plat-v")

    # 用户配置同名个人密钥（平台轨不应读取）
    r = await client.put(
        "/api/my/secrets",
        headers=user_headers,
        json={"key_name": "DINGTALK_APP_SECRET", "value": "user-v", "scope": ""},
    )
    assert r.status_code == 200, r.text
    # 普通用户订阅后可调用
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text

    cfg_admin, status, body = await authorize_capability_gateway(
        _scope(admin_headers["Authorization"]), name
    )
    assert status is None and cfg_admin is not None, body
    cfg_user, status, body = await authorize_capability_gateway(
        _scope(user_headers["Authorization"]), name
    )
    assert status is None and cfg_user is not None, body

    for cfg in (cfg_admin, cfg_user):
        assert cfg["env"]["DINGTALK_APP_SECRET"] == "plat-v"
        assert cfg["_user_env"]["DINGTALK_APP_SECRET"] == "plat-v"
    assert cfg_admin["env"]["DINGTALK_APP_SECRET"] == cfg_user["env"]["DINGTALK_APP_SECRET"]
    assert "user-v" not in cfg_user["env"]["DINGTALK_APP_SECRET"]


@pytest.mark.asyncio
async def test_install_mcp_injects_platform_env(
    client, publisher_headers, admin_headers, user_headers, monkeypatch
):
    """安装探测（marketplace.install_mcp）使用平台密钥，不读个人密钥。"""
    from app.auth import get_current_user
    from app.database import SessionLocal
    from app.models import Capability
    from app.services.marketplace import install_mcp
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    name = "平台安装能力"
    cap_id = await _publish_capability(
        client,
        publisher_headers,
        admin_headers,
        name,
        "mcp",
        _mcp_zip(name, {"ERP_TOKEN": "${ERP_TOKEN}"}),
    )
    await _set_platform_secret(client, admin_headers, cap_id, "ERP_TOKEN", "plat-install")
    await client.put(
        "/api/my/secrets",
        headers=user_headers,
        json={"key_name": "ERP_TOKEN", "value": "user-install", "scope": ""},
    )

    captured: dict = {}

    async def fake_probe(cfg, files=None):
        captured.update(cfg)
        return []

    monkeypatch.setattr("app.services.mcp_gateway.probe_tools", fake_probe)

    token = user_headers["Authorization"].split(" ", 1)[1]
    async with SessionLocal() as db:
        user = await get_current_user(token, db)
        cap = await db.scalar(
            select(Capability)
            .options(selectinload(Capability.artifacts))
            .where(Capability.name == name, Capability.status == "published")
        )
        result = await install_mcp(db, user, cap, {})
    assert result["installed"] is True, result
    assert result["secrets_injected"] == ["ERP_TOKEN"]
    assert captured["env"]["ERP_TOKEN"] == "plat-install"


@pytest.mark.asyncio
async def test_runtime_mcp_connect_injects_platform_env(
    client, publisher_headers, admin_headers, user_headers, monkeypatch
):
    """调试连接（runtime mcp_connect → MCPBridge）使用平台密钥。"""
    name = "平台连接能力"
    cap_id = await _publish_capability(
        client,
        publisher_headers,
        admin_headers,
        name,
        "mcp",
        _mcp_zip(name, {"DB_PASSWORD": "${DB_PASSWORD}"}),
    )
    await _set_platform_secret(client, admin_headers, cap_id, "DB_PASSWORD", "plat-connect")
    await client.put(
        "/api/my/secrets",
        headers=user_headers,
        json={"key_name": "DB_PASSWORD", "value": "user-connect", "scope": ""},
    )
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text

    captured: dict = {}

    class _FakeBridge:
        def __init__(self, gateway_loader=None, env=None, **kwargs):
            captured["env"] = dict(env or {})
            self.tool_defs = []
            self.tool_names = []

        async def connect_capability(self, mcp_name, cap):
            return {"connected": True}

        async def close(self):
            return None

    monkeypatch.setattr("app.services.mcp_bridge.MCPBridge", _FakeBridge)

    r = await client.post(f"/api/runtime/mcp/{name}/connect", headers=user_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["connected"] is True
    assert body["secrets_injected"] == ["DB_PASSWORD"]
    assert captured["env"]["DB_PASSWORD"] == "plat-connect"


@pytest.mark.asyncio
async def test_platform_secrets_follow_name_across_versions(
    client, publisher_headers, admin_headers
):
    """平台密钥按能力名跨版本：配置一次，新发布版本解析同样命中。"""
    from app.database import SessionLocal
    from app.services.capability_secrets import resolve_capability_env

    name = "平台跨版本能力"
    cap_id = await _publish_capability(
        client,
        publisher_headers,
        admin_headers,
        name,
        "mcp",
        _mcp_zip(name, {"APP_SECRET": "${APP_SECRET}"}),
    )
    await _set_platform_secret(client, admin_headers, cap_id, "APP_SECRET", "cross-v")

    # 重发布 1.0.1
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/versions",
        headers=publisher_headers,
        json={"new_version": "1.0.1", "changelog": "patch"},
    )
    assert r.status_code == 201, r.text
    v2 = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{v2}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", _mcp_zip(name, {"APP_SECRET": "${APP_SECRET}"}), "application/zip")},
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/publish/capabilities/{v2}/submit", headers=publisher_headers)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/admin/capabilities/{v2}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text

    # 新版本 API 读出同名密钥
    r = await client.get(f"/api/capabilities/{v2}/platform-secrets", headers=admin_headers)
    assert r.status_code == 200, r.text
    assert [i["key_name"] for i in r.json()["items"]] == ["APP_SECRET"]

    # 解析按名跨版本命中
    async with SessionLocal() as db:
        env = await resolve_capability_env(db, name)
    assert env == {"APP_SECRET": "cross-v"}
