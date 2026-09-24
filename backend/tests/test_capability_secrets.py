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


async def _create_user(client, admin_headers, username: str, role: str = "user") -> dict:
    r = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "secret123",
            "role": role,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _login(client, username: str) -> dict:
    r = await client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _publish_new_version(
    client, publisher_headers, admin_headers, cap_id: str, name: str, new_version: str
) -> str:
    """基于既有行重发布一个新版本，返回新行 id。"""
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/versions",
        headers=publisher_headers,
        json={"new_version": new_version, "changelog": "patch"},
    )
    assert r.status_code == 201, r.text
    v2 = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{v2}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", _mcp_zip(name), "application/zip")},
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
    return v2


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


# ---------- 同名归属与 canonical 行防投毒 ----------


@pytest.mark.asyncio
async def test_create_and_rename_same_name_other_author_forbidden(
    client, publisher_headers, admin_headers
):
    """同名跨作者：创建草稿与改名均 403（防抢注同名后投毒平台密钥）。"""
    name = "名称归属能力"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "mcp", _mcp_zip(name)
    )
    await _create_user(client, admin_headers, "name-thief")
    thief_headers = await _login(client, "name-thief")

    # 他人创建同名草稿 → 403
    r = await client.post(
        "/api/publish/capabilities",
        headers=thief_headers,
        json={"name": name, "type": "tool", "version": "9.9.9", "description": "抢注"},
    )
    assert r.status_code == 403, r.text
    assert "占用" in r.json()["detail"]

    # 他人把自己的草稿改名到该名称 → 403
    r = await client.post(
        "/api/publish/capabilities",
        headers=thief_headers,
        json={"name": "thief-draft", "type": "tool", "version": "1.0.0"},
    )
    assert r.status_code == 201, r.text
    draft_id = r.json()["id"]
    r = await client.put(
        f"/api/publish/capabilities/{draft_id}",
        headers=thief_headers,
        json={"name": name},
    )
    assert r.status_code == 403, r.text
    assert "占用" in r.json()["detail"]

    # 同作者同名新版本仍允许
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": name, "type": "tool", "version": "9.9.9", "description": "同作者新版本"},
    )
    assert r.status_code == 201, r.text


@pytest.mark.asyncio
async def test_platform_secrets_reject_foreign_same_name_row(
    client, publisher_headers, admin_headers
):
    """直接构造他人同名行：平台密钥 GET/PUT 均 403，正主值不被投毒。"""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Capability, User
    from app.services.capability_secrets import resolve_capability_env

    name = "密钥正主能力"
    cap_id = await _publish_capability(
        client,
        publisher_headers,
        admin_headers,
        name,
        "mcp",
        _mcp_zip(name, {"APP_SECRET": "${APP_SECRET}"}),
    )
    await _set_platform_secret(client, admin_headers, cap_id, "APP_SECRET", "victim-v")

    await _create_user(client, admin_headers, "secret-thief")
    async with SessionLocal() as db:
        thief = await db.scalar(select(User).where(User.username == "secret-thief"))
        forged = Capability(
            name=name,
            type="mcp",
            version="9.9.9",
            status="draft",
            author_id=thief.id,
            visibility="internal",
        )
        db.add(forged)
        await db.commit()
        await db.refresh(forged)
        forged_id = forged.id

    thief_headers = await _login(client, "secret-thief")
    r = await client.get(
        f"/api/capabilities/{forged_id}/platform-secrets", headers=thief_headers
    )
    assert r.status_code == 403, r.text
    r = await client.put(
        f"/api/capabilities/{forged_id}/platform-secrets",
        headers=thief_headers,
        json={"secrets": {"APP_SECRET": "poisoned"}},
    )
    assert r.status_code == 403, r.text

    # 正主值未被投毒；canonical 作者（publisher）仍可管理
    async with SessionLocal() as db:
        env = await resolve_capability_env(db, name)
    assert env == {"APP_SECRET": "victim-v"}
    r = await client.get(
        f"/api/capabilities/{cap_id}/platform-secrets", headers=publisher_headers
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_platform_secrets_canonical_author_manages_multi_version(
    client, publisher_headers, admin_headers
):
    """canonical 作者对自己的多版本行均可管理（published 作者即 canonical）。"""
    name = "多版本正主"
    cap_id = await _publish_capability(
        client,
        publisher_headers,
        admin_headers,
        name,
        "mcp",
        _mcp_zip(name, {"K": "${K}"}),
    )
    v2 = await _publish_new_version(client, publisher_headers, admin_headers, cap_id, name, "1.0.1")

    # 新行（published）可写；旧行（deprecated，同作者）也可读写
    r = await client.put(
        f"/api/capabilities/{v2}/platform-secrets",
        headers=publisher_headers,
        json={"secrets": {"K": "v2"}},
    )
    assert r.status_code == 200, r.text
    r = await client.get(
        f"/api/capabilities/{cap_id}/platform-secrets", headers=publisher_headers
    )
    assert r.status_code == 200, r.text
    assert [i["key_name"] for i in r.json()["items"]] == ["K"]


@pytest.mark.asyncio
async def test_relay_platform_secret_decrypt_failure_returns_502(
    client, publisher_headers, admin_headers
):
    """relay 中平台密钥解密失败：返回 502 JSON（不裸 500、不泄露明文）。"""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import CapabilitySecret
    from app.services.mcp_gateway import authorize_capability_gateway

    name = "解密失败能力"
    cap_id = await _publish_capability(
        client,
        publisher_headers,
        admin_headers,
        name,
        "mcp",
        _mcp_zip(name, {"K": "${K}"}),
    )
    await _set_platform_secret(client, admin_headers, cap_id, "K", "v")
    async with SessionLocal() as db:
        row = await db.scalar(
            select(CapabilitySecret).where(CapabilitySecret.capability_name == name)
        )
        row.ciphertext = "not-a-valid-fernet-token"
        await db.commit()

    cfg, status, body = await authorize_capability_gateway(
        _scope(admin_headers["Authorization"]), name
    )
    assert cfg is None and status == 502, body
    assert "密钥" in body["detail"] or "解析" in body["detail"]


# ---------- 其余创建入口的名称归属 ----------


def _plugin_with_skill_zip(plugin_name: str, skill_name: str) -> bytes:
    """构造只含一个 skill 组件的 plugin 包（skill 名可控）。"""
    files = {
        "plugin.json": json.dumps(
            {"name": plugin_name, "version": "1.0.0"}, ensure_ascii=False
        ).encode("utf-8"),
        f"skills/{skill_name}/SKILL.md": "# 组件\n".encode("utf-8"),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_workflow_create_same_name_other_author_forbidden(
    client, publisher_headers, admin_headers
):
    """工作流创建入口也须归属校验（实测投毒路径）。"""
    name = "工作流占名能力"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "mcp", _mcp_zip(name)
    )
    await _create_user(client, admin_headers, "wf-thief")
    thief_headers = await _login(client, "wf-thief")

    r = await client.post(
        "/api/workflows",
        headers=thief_headers,
        json={"name": name, "version": "1.0.0", "workflow": {"nodes": [], "edges": []}},
    )
    assert r.status_code == 403, r.text
    assert "占用" in r.json()["detail"]


@pytest.mark.asyncio
async def test_assemble_create_same_name_other_author_forbidden(
    client, publisher_headers, admin_headers
):
    """Agent 组装入口也须归属校验（实测投毒路径）。"""
    from test_workflow import _agent_zip, _tool_zip

    persona = "assemble-own-persona"
    victim_name = "assemble-victim-cap"
    await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona)
    )
    await _publish_capability(
        client, publisher_headers, admin_headers, victim_name, "tool", _tool_zip(victim_name)
    )
    await _create_user(client, admin_headers, "assemble-thief", role="publisher")
    thief_headers = await _login(client, "assemble-thief")

    r = await client.post(
        "/api/assemble/agents",
        headers=thief_headers,
        json={
            "persona": persona,
            "name": victim_name,
            "version": "0.1.0",
            "dependencies": [],
        },
    )
    assert r.status_code == 403, r.text
    assert "占用" in r.json()["detail"]


@pytest.mark.asyncio
async def test_plugin_component_same_name_other_author_forbidden(
    client, publisher_headers, admin_headers
):
    """plugin 组件名撞他人能力名：上传即 403（防借组件创建同名行）。"""
    victim_name = "组件占名技能"
    await _publish_capability(
        client, publisher_headers, admin_headers, victim_name, "mcp", _mcp_zip(victim_name)
    )
    await _create_user(client, admin_headers, "plugin-thief", role="publisher")
    thief_headers = await _login(client, "plugin-thief")

    r = await client.post(
        "/api/publish/capabilities",
        headers=thief_headers,
        json={"name": "thief-plugin", "type": "plugin", "version": "1.0.0"},
    )
    assert r.status_code == 201, r.text
    plugin_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{plugin_id}/artifact",
        headers=thief_headers,
        files={"file": ("p.zip", _plugin_with_skill_zip("thief-plugin", victim_name), "application/zip")},
    )
    assert r.status_code == 403, r.text
    assert "占用" in r.json()["detail"]


# ---------- canonical：deprecated 纳入 / 非 semver 容错 ----------


@pytest.mark.asyncio
async def test_canonical_author_keeps_deprecated_row(
    client, publisher_headers, admin_headers
):
    """能力弃用后 canonical 仍为原作者；攻击者更高版本行不能夺走管理权。"""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Capability, User
    from app.services.capability_secrets import (
        canonical_capability_author_id,
        resolve_capability_env,
    )

    name = "弃用正主能力"
    cap_id = await _publish_capability(
        client,
        publisher_headers,
        admin_headers,
        name,
        "mcp",
        _mcp_zip(name, {"APP_SECRET": "${APP_SECRET}"}),
    )
    await _set_platform_secret(client, admin_headers, cap_id, "APP_SECRET", "victim-v")
    r = await client.post(f"/api/admin/capabilities/{cap_id}/deprecate", headers=admin_headers)
    assert r.status_code == 200, r.text

    publisher_id = (await client.get("/api/auth/me", headers=publisher_headers)).json()["id"]
    await _create_user(client, admin_headers, "deprecate-thief")
    async with SessionLocal() as db:
        thief = await db.scalar(select(User).where(User.username == "deprecate-thief"))
        forged = Capability(
            name=name,
            type="mcp",
            version="9.9.9",
            status="draft",
            author_id=thief.id,
            visibility="internal",
        )
        db.add(forged)
        await db.commit()
        await db.refresh(forged)
        forged_id = forged.id
        assert await canonical_capability_author_id(db, name) == publisher_id

    thief_headers = await _login(client, "deprecate-thief")
    r = await client.put(
        f"/api/capabilities/{forged_id}/platform-secrets",
        headers=thief_headers,
        json={"secrets": {"APP_SECRET": "poisoned"}},
    )
    assert r.status_code == 403, r.text
    async with SessionLocal() as db:
        env = await resolve_capability_env(db, name)
    assert env == {"APP_SECRET": "victim-v"}


@pytest.mark.asyncio
async def test_non_semver_version_canonical_endpoints_ok(
    client, publisher_headers, admin_headers
):
    """非 semver 版本行（WorkflowCreate 无 semver 校验，落库后响应序列化才报错）
    不得让 canonical 判定 500。"""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Capability, User
    from app.services.capability_secrets import resolve_capability_env

    name = "非语义版本流"
    await _create_user(client, admin_headers, "nonsemver-owner")
    async with SessionLocal() as db:
        owner = await db.scalar(select(User).where(User.username == "nonsemver-owner"))
        cap = Capability(
            name=name,
            type="workflow",
            version="weird-version",
            status="published",
            author_id=owner.id,
            visibility="internal",
        )
        db.add(cap)
        await db.commit()
        await db.refresh(cap)
        cap_id = cap.id
    owner_headers = await _login(client, "nonsemver-owner")

    r = await client.get(f"/api/capabilities/{cap_id}/platform-secrets", headers=owner_headers)
    assert r.status_code == 200, r.text
    r = await client.put(
        f"/api/capabilities/{cap_id}/platform-secrets",
        headers=owner_headers,
        json={"secrets": {"K": "v"}},
    )
    assert r.status_code == 200, r.text
    async with SessionLocal() as db:
        env = await resolve_capability_env(db, name)
    assert env == {"K": "v"}
    r = await client.delete(
        f"/api/capabilities/{cap_id}/platform-secrets/K", headers=owner_headers
    )
    assert r.status_code == 200, r.text


# ---------- 最后一行删除/改名的密钥清理 ----------


@pytest.mark.asyncio
async def test_delete_last_row_clears_platform_secrets(
    client, publisher_headers, admin_headers
):
    """删除同名最后一行：清空该 name 的平台密钥；仍有其他行时保留。"""
    from app.database import SessionLocal
    from app.services.capability_secrets import list_capability_secrets

    name = "清理密钥能力"

    async def _create_draft(version: str) -> str:
        r = await client.post(
            "/api/publish/capabilities",
            headers=publisher_headers,
            json={"name": name, "type": "tool", "version": version},
        )
        assert r.status_code == 201, r.text
        return r.json()["id"]

    first = await _create_draft("1.0.0")
    second = await _create_draft("2.0.0")
    await _set_platform_secret(client, publisher_headers, first, "K", "v")

    r = await client.delete(f"/api/publish/capabilities/{first}", headers=publisher_headers)
    assert r.status_code == 200, r.text
    async with SessionLocal() as db:
        assert await list_capability_secrets(db, name)  # 仍有同名行 → 保留

    r = await client.delete(f"/api/publish/capabilities/{second}", headers=publisher_headers)
    assert r.status_code == 200, r.text
    async with SessionLocal() as db:
        assert await list_capability_secrets(db, name) == []

    # 同作者重建同名草稿：无残留元数据
    rebuilt = await _create_draft("3.0.0")
    r = await client.get(
        f"/api/capabilities/{rebuilt}/platform-secrets", headers=publisher_headers
    )
    assert r.status_code == 200, r.text
    assert r.json()["items"] == []


@pytest.mark.asyncio
async def test_rename_last_row_clears_old_name_secrets(client, publisher_headers, admin_headers):
    """最后一行改名：清理旧 name 的平台密钥，避免名称释放后孤儿密钥被认领。"""
    from app.database import SessionLocal
    from app.services.capability_secrets import list_capability_secrets

    old_name = "改名清理旧名"
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": old_name, "type": "tool", "version": "1.0.0"},
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    await _set_platform_secret(client, publisher_headers, cap_id, "K", "v")

    r = await client.put(
        f"/api/publish/capabilities/{cap_id}",
        headers=publisher_headers,
        json={"name": "改名清理新名"},
    )
    assert r.status_code == 200, r.text
    async with SessionLocal() as db:
        assert await list_capability_secrets(db, old_name) == []


@pytest.mark.asyncio
async def test_whitespace_name_normalized_and_no_ownership_bypass(
    client, publisher_headers, admin_headers
):
    """写入端统一 strip：带空白名落库为 strip 后名称，且无法绕过归属判定。"""
    from sqlalchemy import select

    from app.database import SessionLocal
    from app.models import Capability

    name = "空格归属能力"
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": f"{name} ", "type": "tool", "version": "1.0.0"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == name  # 带尾空格建名 → 落库为 strip 后名称
    cap_id = r.json()["id"]
    async with SessionLocal() as db:
        row = await db.scalar(select(Capability).where(Capability.id == cap_id))
        assert row.name == name

    await _create_user(client, admin_headers, "space-thief")
    thief_headers = await _login(client, "space-thief")
    # 精确同名（换版本避开 409）→ 403 归属拦截
    r = await client.post(
        "/api/publish/capabilities",
        headers=thief_headers,
        json={"name": name, "type": "tool", "version": "1.0.1"},
    )
    assert r.status_code == 403, r.text
    # 空白变体（strip 后同名）同样 403：规范化后归属判定命中
    r = await client.post(
        "/api/publish/capabilities",
        headers=thief_headers,
        json={"name": f" {name} ", "type": "tool", "version": "1.0.2"},
    )
    assert r.status_code == 403, r.text
    assert "占用" in r.json()["detail"]

    # 纯空白名 → 422（normalize 拒绝空名）
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "   ", "type": "tool", "version": "9.9.9"},
    )
    assert r.status_code == 422, r.text
