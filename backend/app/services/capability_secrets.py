"""能力级平台密钥（全用户共用）：加密存库、按能力名解析注入。

与用户个人密钥（secret_vault 的 user_secrets）分工：平台轨（网关 / runtime /
安装探测）一律注入本模块的能力级密钥，调用者身份不改变注入内容。
"""

from __future__ import annotations

import json
import logging

from fastapi import HTTPException, status
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Capability, CapabilitySecret
from app.services.capabilities import parse_semver
from app.services.secret_vault import (
    decrypt_value,
    encrypt_value,
    resolve_user_env,
    validate_key_name,
)

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
    """能力声明的环境变量名（固定配置项）：input_schema 的 required_env/env，回退包内 connection.json env。

    与 dashboard 投影（services/dashboard_consume.py）的口径保持一致，便于前端提示。
    binding=user/user_only 时，个人凭据键即本清单（配置项固定，平台/个人只是值来源不同）。
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


# 「个人凭据缺失」机器可读标记（响应头）：agent 侧据此在管理员身份下用平台凭据重试。
# 与 agent/src/tools/market_execute.py 的常量保持一致，改动需双端同步。
USER_CREDS_MISSING_CODE = "user_credentials_missing"
USER_CREDS_MISSING_HEADER = "X-Market-Error-Code"


def is_user_bound(cap) -> bool:
    """能力是否按提问者身份执行（binding=user / user_only）；默认 service（平台身份）。"""
    return (getattr(cap, "binding", None) or "service") in ("user", "user_only")


def is_user_only(cap) -> bool:
    """能力是否仅允许用户身份执行（binding=user_only，不提供平台兜底）。

    平台身份（服务令牌：管理员兜底重试/系统任务）一律拒绝；缺个人凭据也不发
    管理员重试标记（无平台可兜底）。
    """
    return (getattr(cap, "binding", None) or "service") == "user_only"


def is_service_identity(user) -> bool:
    """调用者是否服务令牌身份（纯平台身份，无个人维度）。

    使用场景：agent 管理员/系统任务以服务令牌调用 runtime（个人凭据缺失后重试、
    或本就没有市场用户令牌时直接执行），market 直接以平台凭据执行。
    """
    from app.services.service_tokens import is_service_token

    return is_service_token(user)


async def resolve_user_bound_env(
    db: AsyncSession,
    user_id: str,
    cap,
) -> tuple[dict[str, str], list[str]]:
    """解析 user 级能力的**个人**凭据，返回 ``(env, missing_keys)``。

    missing 非空即 fail-closed（由调用方决定提示/管理员重试）；能力未声明配置项时
    返回空集合（无个人凭据需求）。
    """
    keys = declared_env_keys(cap)
    if not keys:
        return {}, []
    env = await resolve_user_env(db, user_id, capability_id=cap.id, keys=keys)
    env = {k: v for k, v in env.items() if (v or "").strip()}
    missing = [k for k in keys if k not in env]
    return env, missing


async def resolve_user_bound_env_for(
    db: AsyncSession,
    cap,
    user,
    *,
    platform_env: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """user 级能力的执行凭据（按调用者身份分流，market 不判断任何角色）。

    - **服务令牌**（平台身份）→ 直接用平台凭据（缺平台键即计入 missing）；
    - **用户令牌** → 个人凭据，缺任一键即计入 missing（由调用方 fail-closed）。
    """
    keys = declared_env_keys(cap)
    if not keys:
        return {}, []
    if is_service_identity(user):
        plat = platform_env
        if plat is None:
            plat = await resolve_capability_env(db, cap.name)
        env: dict[str, str] = {}
        for key in keys:
            value = (plat.get(key) or "").strip()
            if value:
                env[key] = value
        return env, [k for k in keys if k not in env]
    return await resolve_user_bound_env(db, user.id, cap)


async def require_user_bound_env(
    db: AsyncSession,
    cap,
    user,
    platform_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """user 级能力执行凭据（fail-closed 校验版）。

    user_only 能力：平台身份（服务令牌）一律 403 拒绝；缺个人凭据不发重试标记。
    平台身份（服务令牌）缺平台密钥 → 403 提示管理员配置；
    用户令牌缺个人凭据 → 403（binding=user 带 ``X-Market-Error-Code: user_credentials_missing``
    响应头，agent 侧管理员据此以平台身份重试一次）。
    """
    if is_user_only(cap) and is_service_identity(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "该能力仅支持按用户身份执行（不提供平台兜底）；请使用个人凭据调用",
        )
    if platform_env is None:
        platform_env = await resolve_capability_env(db, cap.name)
    env, missing = await resolve_user_bound_env_for(db, cap, user, platform_env=platform_env)
    if missing:
        what = "、".join(missing)
        if is_service_identity(user):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"该能力按平台身份执行，缺少平台密钥：{what}；"
                "请管理员在能力详情中配置平台密钥后重试",
            )
        # user_only 无平台可兜底: 不带管理员重试标记, 直接引导配置个人凭据
        headers = (
            None if is_user_only(cap)
            else {USER_CREDS_MISSING_HEADER: USER_CREDS_MISSING_CODE}
        )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"该能力按你的身份执行，缺少必需凭据：{what}；"
            "请在能力市场该能力卡片上点击「配置凭据」填写后重试",
            headers=headers,
        )
    return env
