"""能力级平台密钥（全用户共用）：加密存库、按能力名解析注入。

与用户个人密钥（secret_vault 的 user_secrets）分工：平台轨（网关 / runtime /
安装探测）一律注入本模块的能力级密钥，调用者身份（含 act-as 目标）不改变注入内容。
"""

from __future__ import annotations

import json
import logging

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Capability, CapabilitySecret
from app.services.capabilities import parse_semver
from app.services.secret_vault import decrypt_value, encrypt_value, validate_key_name

logger = logging.getLogger("market.capability_secrets")


def _norm_name(name: str) -> str:
    return (name or "").strip()


def _version_sort_key(cap: Capability) -> tuple[int, int, int]:
    """版本排序键：非 semver（workflow 等入口允许）按最低优先级兜底，不抛异常。"""
    try:
        return parse_semver(cap.version)
    except (ValueError, TypeError):
        return (-1, -1, -1)


async def canonical_capability_author_id(db: AsyncSession, name: str) -> str | None:
    """该能力名 canonical 行的作者 id（平台密钥归属判定用）。

    canonical 口径：优先已发布/弃用（published ∪ deprecated）行并取其中最高版本；
    池空时退回全部行中的最高版本（草稿/审核中亦可管理）。
    """
    cap_name = _norm_name(name)
    rows = list(
        (await db.scalars(select(Capability).where(Capability.name == cap_name))).all()
    )
    if not rows:
        return None
    active = [c for c in rows if c.status in ("published", "deprecated")]
    pool = active or rows
    canonical = max(pool, key=_version_sort_key)
    return canonical.author_id


async def list_capability_secrets(db: AsyncSession, name: str) -> list[CapabilitySecret]:
    """列出某能力名下的平台密钥元数据（不含明文）。"""
    rows = await db.scalars(
        select(CapabilitySecret)
        .where(CapabilitySecret.capability_name == _norm_name(name))
        .order_by(CapabilitySecret.key_name)
    )
    return list(rows)


async def upsert_capability_secrets_bulk(
    db: AsyncSession,
    name: str,
    secrets: dict[str, str],
    updated_by: str,
) -> list[CapabilitySecret]:
    """按能力名批量写入平台密钥（空值跳过），返回本次写入的行。"""
    cap_name = _norm_name(name)
    rows: list[CapabilitySecret] = []
    for raw_key, raw_value in (secrets or {}).items():
        if raw_value is None or str(raw_value).strip() == "":
            continue
        key = validate_key_name(raw_key)
        existing = await db.scalar(
            select(CapabilitySecret).where(
                CapabilitySecret.capability_name == cap_name,
                CapabilitySecret.key_name == key,
            )
        )
        cipher = encrypt_value(str(raw_value))
        if existing is None:
            row = CapabilitySecret(
                capability_name=cap_name,
                key_name=key,
                ciphertext=cipher,
                updated_by=updated_by,
            )
            db.add(row)
        else:
            existing.ciphertext = cipher
            existing.updated_by = updated_by
            row = existing
        await db.flush()
        rows.append(row)
    return rows


async def delete_capability_secret(db: AsyncSession, name: str, key_name: str) -> None:
    key = validate_key_name(key_name)
    row = await db.scalar(
        select(CapabilitySecret).where(
            CapabilitySecret.capability_name == _norm_name(name),
            CapabilitySecret.key_name == key,
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "平台密钥不存在")
    await db.delete(row)
    await db.flush()


async def delete_capability_secrets_by_name(db: AsyncSession, name: str) -> int:
    """删除某能力名下的全部平台密钥（能力名最后一行删除/改名时调用）。返回删除条数。"""
    cap_name = _norm_name(name)
    if not cap_name:
        return 0
    result = await db.execute(
        delete(CapabilitySecret).where(CapabilitySecret.capability_name == cap_name)
    )
    return int(result.rowcount or 0)


async def resolve_capability_env(db: AsyncSession, name: str) -> dict[str, str]:
    """按能力名解析平台密钥明文（跨版本共用）。绝不写日志。"""
    rows = await list_capability_secrets(db, name)
    return {row.key_name: decrypt_value(row.ciphertext) for row in rows}


def declared_env_keys(cap) -> list[str]:
    """能力声明的环境变量名：input_schema 的 required_env/env，回退包内 connection.json env。

    与 dashboard 投影（services/dashboard_consume.py）的口径保持一致，便于前端提示。
    """
    schema = getattr(cap, "input_schema", None) or {}
    keys: list[str] = []
    if isinstance(schema, dict):
        required = schema.get("required_env")
        if isinstance(required, list):
            keys.extend(str(k) for k in required if str(k).strip())
        hint = schema.get("env")
        if isinstance(hint, dict):
            keys.extend(str(k) for k in hint if str(k).strip())
    if not keys and getattr(cap, "type", "") == "mcp":
        # 老包缺 input_schema 时回退读包内 connection.json 的 env 键
        try:
            from app.services.mcp_gateway import read_package_files

            raw = read_package_files(cap).get("connection.json")
            conn = json.loads(raw.decode("utf-8-sig")) if raw else {}
            env = conn.get("env") if isinstance(conn, dict) else None
            if isinstance(env, dict):
                keys.extend(str(k) for k in env if str(k).strip())
        except Exception as exc:  # noqa: BLE001
            logger.debug(
                "declared_env 读取能力包失败 cap=%s: %s", getattr(cap, "name", "?"), exc
            )
    return sorted(dict.fromkeys(keys))
