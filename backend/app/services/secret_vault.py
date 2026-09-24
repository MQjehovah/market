"""用户业务密钥保险箱：Fernet 加密存库，调用时按用户注入 env/占位符。"""

from __future__ import annotations

import base64
import hashlib
import re
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import UserSecret

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$")


def _fernet() -> Fernet:
    settings = get_settings()
    raw = (settings.secret_vault_key or "").strip()
    if raw:
        try:
            return Fernet(raw.encode("utf-8"))
        except (ValueError, TypeError, Exception):
            digest = hashlib.sha256(raw.encode("utf-8")).digest()
            return Fernet(base64.urlsafe_b64encode(digest))
    digest = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_value(plaintext: str) -> str:
    return _fernet().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt_value(ciphertext: str) -> str:
    try:
        return _fernet().decrypt(ciphertext.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError) as exc:
        raise HTTPException(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "密钥解密失败：请检查 SECRET_VAULT_KEY / JWT_SECRET 是否变更",
        ) from exc


def validate_key_name(key_name: str) -> str:
    name = (key_name or "").strip()
    if not _KEY_RE.match(name):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"非法密钥名: {key_name!r}（需符合环境变量命名）",
        )
    return name


def to_secret_out(row: UserSecret) -> dict[str, Any]:
    return {
        "id": row.id,
        "key_name": row.key_name,
        "scope": row.scope or "",
        "label": row.label or "",
        "has_value": bool(row.ciphertext),
        "updated_at": row.updated_at,
        "created_at": row.created_at,
    }


async def list_secrets(
    db: AsyncSession,
    user_id: str,
    *,
    scope: str | None = None,
) -> list[UserSecret]:
    stmt = select(UserSecret).where(UserSecret.user_id == user_id)
    if scope is not None:
        stmt = stmt.where(UserSecret.scope == (scope or ""))
    stmt = stmt.order_by(UserSecret.key_name, UserSecret.scope)
    return list((await db.scalars(stmt)).all())


async def upsert_secret(
    db: AsyncSession,
    user_id: str,
    *,
    key_name: str,
    value: str,
    scope: str = "",
    label: str = "",
) -> UserSecret:
    key = validate_key_name(key_name)
    scope_id = (scope or "").strip()
    if not isinstance(value, str) or value == "":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "密钥值不能为空")
    existing = await db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == user_id,
            UserSecret.key_name == key,
            UserSecret.scope == scope_id,
        )
    )
    cipher = encrypt_value(value)
    if existing is None:
        row = UserSecret(
            user_id=user_id,
            key_name=key,
            scope=scope_id,
            ciphertext=cipher,
            label=(label or "").strip()[:128],
        )
        db.add(row)
        await db.flush()
        return row
    existing.ciphertext = cipher
    if (label or "").strip():
        existing.label = (label or "").strip()[:128]
    await db.flush()
    return existing


async def upsert_secrets_bulk(
    db: AsyncSession,
    user_id: str,
    *,
    secrets: dict[str, str],
    scope: str = "",
) -> list[UserSecret]:
    rows: list[UserSecret] = []
    for key, value in (secrets or {}).items():
        if value is None or str(value).strip() == "":
            continue
        rows.append(
            await upsert_secret(
                db,
                user_id,
                key_name=key,
                value=str(value),
                scope=scope,
            )
        )
    return rows


async def delete_secret(db: AsyncSession, user_id: str, secret_id: str) -> None:
    row = await db.get(UserSecret, secret_id)
    if row is None or row.user_id != user_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "密钥不存在")
    await db.delete(row)
    await db.flush()


async def delete_secret_by_key(
    db: AsyncSession,
    user_id: str,
    *,
    key_name: str,
    scope: str = "",
) -> None:
    key = validate_key_name(key_name)
    row = await db.scalar(
        select(UserSecret).where(
            UserSecret.user_id == user_id,
            UserSecret.key_name == key,
            UserSecret.scope == (scope or ""),
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "密钥不存在")
    await db.delete(row)
    await db.flush()


async def resolve_user_env(
    db: AsyncSession,
    user_id: str,
    *,
    capability_id: str | None = None,
    keys: list[str] | None = None,
) -> dict[str, str]:
    """解析用户可用的明文 env：能力级覆盖全局。绝不写日志。"""
    rows = await list_secrets(db, user_id)
    out: dict[str, str] = {}
    # 先全局
    for row in rows:
        if row.scope:
            continue
        if keys is not None and row.key_name not in keys:
            continue
        out[row.key_name] = decrypt_value(row.ciphertext)
    # 再能力级覆盖
    cap_id = (capability_id or "").strip()
    if cap_id:
        for row in rows:
            if row.scope != cap_id:
                continue
            if keys is not None and row.key_name not in keys:
                continue
            out[row.key_name] = decrypt_value(row.ciphertext)
    return out


async def secret_status(
    db: AsyncSession,
    user_id: str,
    *,
    required_keys: list[str],
    capability_id: str | None = None,
) -> dict[str, Any]:
    env = await resolve_user_env(db, user_id, capability_id=capability_id, keys=required_keys)
    filled = [k for k in required_keys if env.get(k)]
    missing = [k for k in required_keys if k not in env]
    return {
        "required": list(required_keys),
        "filled": filled,
        "missing": missing,
        "complete": len(missing) == 0,
    }


def attach_user_env(config: dict[str, Any], user_env: dict[str, str]) -> dict[str, Any]:
    """把用户密钥挂到 connect 配置上，供 resolve_placeholders / stdio env 合并。"""
    if not user_env:
        return config
    cfg = dict(config)
    cfg["_user_env"] = dict(user_env)
    # 空字符串 / 纯占位的 env 项用托管值填入，便于明文直传场景
    env = dict(cfg.get("env") or {})
    for k, v in user_env.items():
        cur = env.get(k)
        if cur is None or str(cur).strip() == "" or (
            isinstance(cur, str) and cur.strip().startswith("${") and cur.strip().endswith("}")
        ):
            env[k] = v
    cfg["env"] = env
    return cfg


def attach_platform_env(config: dict[str, Any], platform_env: dict[str, str]) -> dict[str, Any]:
    """把能力级平台密钥挂到 connect 配置上（合并逻辑与 attach_user_env 一致）。"""
    return attach_user_env(config, platform_env)
