"""用户业务密钥托管：加密存库、状态查询、占位符注入。"""

import pytest

from app.services.mcp_gateway import resolve_placeholders
from app.services.secret_vault import attach_user_env, encrypt_value, decrypt_value


def test_fernet_roundtrip():
    plain = "erp-secret-value-42"
    cipher = encrypt_value(plain)
    assert cipher != plain
    assert decrypt_value(cipher) == plain


def test_resolve_placeholders_prefers_user_env(monkeypatch):
    monkeypatch.setenv("ERP_TOKEN", "from-os")
    out = resolve_placeholders("${ERP_TOKEN}", {"ERP_TOKEN": "from-vault"})
    assert out == "from-vault"
    out2 = resolve_placeholders("${ERP_TOKEN}")
    assert out2 == "from-os"
    out3 = resolve_placeholders("${MISSING:fallback}", {})
    assert out3 == "fallback"


def test_attach_user_env_fills_placeholders():
    cfg = {
        "env": {
            "ERP_TOKEN": "${ERP_TOKEN}",
            "DB_PASSWORD": "",
            "KEEP": "literal",
        }
    }
    attached = attach_user_env(cfg, {"ERP_TOKEN": "t1", "DB_PASSWORD": "p1", "EXTRA": "x"})
    assert attached["env"]["ERP_TOKEN"] == "t1"
    assert attached["env"]["DB_PASSWORD"] == "p1"
    assert attached["env"]["KEEP"] == "literal"
    assert attached["_user_env"]["EXTRA"] == "x"


@pytest.mark.asyncio
async def test_my_secrets_crud_and_status(client, user_headers, admin_headers):
    # 写入
    r = await client.put(
        "/api/my/secrets",
        headers=user_headers,
        json={"key_name": "ERP_API_KEY", "value": "sk-user-1", "scope": "", "label": "ERP"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["key_name"] == "ERP_API_KEY"
    assert body["has_value"] is True
    assert "value" not in body
    assert "ciphertext" not in body
    secret_id = body["id"]

    # 列表不回显明文
    r = await client.get("/api/my/secrets", headers=user_headers)
    assert r.status_code == 200
    items = r.json()
    assert any(i["id"] == secret_id for i in items)
    assert all("ciphertext" not in i and "value" not in i for i in items)

    # status
    r = await client.get(
        "/api/my/secrets/status",
        headers=user_headers,
        params={"keys": "ERP_API_KEY,DB_PASSWORD"},
    )
    assert r.status_code == 200
    st = r.json()
    assert st["complete"] is False
    assert "ERP_API_KEY" in st["filled"]
    assert "DB_PASSWORD" in st["missing"]

    # bulk 能力级
    r = await client.put(
        "/api/my/secrets/bulk",
        headers=user_headers,
        json={"scope": "cap-fake-id", "secrets": {"DB_PASSWORD": "db-pass", "ERP_API_KEY": ""}},
    )
    assert r.status_code == 200, r.text
    assert len(r.json()) == 1  # 空值跳过

    r = await client.get(
        "/api/my/secrets/status",
        headers=user_headers,
        params={"keys": "ERP_API_KEY,DB_PASSWORD", "capability_id": "cap-fake-id"},
    )
    assert r.status_code == 200
    st = r.json()
    assert st["complete"] is True

    # 他人不可删
    r = await client.delete(f"/api/my/secrets/{secret_id}", headers=admin_headers)
    assert r.status_code == 404

    # 本人可删
    r = await client.delete(f"/api/my/secrets/{secret_id}", headers=user_headers)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_resolve_user_env_capability_overrides_global(client, user_headers):
    from app.database import SessionLocal
    from app.services.secret_vault import resolve_user_env
    from app.auth import get_current_user

    await client.put(
        "/api/my/secrets",
        headers=user_headers,
        json={"key_name": "TOKEN", "value": "global-v", "scope": ""},
    )
    await client.put(
        "/api/my/secrets",
        headers=user_headers,
        json={"key_name": "TOKEN", "value": "cap-v", "scope": "cap-1"},
    )

    token = user_headers["Authorization"].split(" ", 1)[1]
    async with SessionLocal() as db:
        user = await get_current_user(token, db)
        env_g = await resolve_user_env(db, user.id)
        assert env_g["TOKEN"] == "global-v"
        env_c = await resolve_user_env(db, user.id, capability_id="cap-1")
        assert env_c["TOKEN"] == "cap-v"


@pytest.mark.asyncio
async def test_my_secret_values_endpoint(client, user_headers, admin_headers):
    """本人密钥明文（本地安装取用）：能力级覆盖、keys/missing、仅本人、不缓存、未登录 401。"""
    r = await client.get("/api/my/secrets/values")
    assert r.status_code == 401

    await client.put(
        "/api/my/secrets",
        headers=user_headers,
        json={"key_name": "TOKEN", "value": "global-v", "scope": ""},
    )
    await client.put(
        "/api/my/secrets",
        headers=user_headers,
        json={"key_name": "TOKEN", "value": "cap-v", "scope": "cap-1"},
    )
    await client.put(
        "/api/my/secrets",
        headers=user_headers,
        json={"key_name": "ONLY_GLOBAL", "value": "g1", "scope": ""},
    )

    # 默认只合并全局；响应不缓存
    r = await client.get("/api/my/secrets/values", headers=user_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["values"] == {"TOKEN": "global-v", "ONLY_GLOBAL": "g1"}
    assert body["missing"] == []
    assert r.headers.get("cache-control") == "no-store"

    # scope：能力级覆盖全局，全局项兜底保留
    r = await client.get(
        "/api/my/secrets/values", headers=user_headers, params={"scope": "cap-1"}
    )
    assert r.status_code == 200, r.text
    assert r.json()["values"] == {"TOKEN": "cap-v", "ONLY_GLOBAL": "g1"}

    # keys 白名单 + missing
    r = await client.get(
        "/api/my/secrets/values",
        headers=user_headers,
        params={"keys": "TOKEN,DB_PASSWORD"},
    )
    body = r.json()
    assert body["values"] == {"TOKEN": "global-v"}
    assert body["missing"] == ["DB_PASSWORD"]

    # 仅查本人：admin 无密钥；即便带他人作用域名也读不到数据
    r = await client.get("/api/my/secrets/values", headers=admin_headers)
    assert r.status_code == 200
    assert r.json()["values"] == {}
    r = await client.get(
        "/api/my/secrets/values", headers=admin_headers, params={"scope": "cap-1"}
    )
    assert r.status_code == 200
    assert r.json()["values"] == {}

    # 既有元数据接口不回归：不回显明文
    r = await client.get("/api/my/secrets", headers=user_headers)
    assert r.status_code == 200
    assert all("value" not in i and "ciphertext" not in i for i in r.json())
