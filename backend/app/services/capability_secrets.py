"""能力级平台密钥（全用户共用）：加密存库、按能力名解析注入。

与用户个人密钥（secret_vault 的 user_secrets）分工：平台轨（网关 / runtime /
安装探测）一律注入本模块的能力级密钥，调用者身份不改变注入内容。
"""

from __future__ import annotations

import json
import logging
import re

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
    binding=user/user&service 时，个人凭据键即本清单（配置项固定，平台/个人只是值来源不同）。
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
    for field in _auth_method_keys(cap):
        if field not in keys:
            keys.append(field)
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


# 「个人凭据缺失」机器可读标记（响应头）：前端据此引导用户配置个人凭据。
# 普通用户不会被平台凭据兜底；market 管理员在服务端直接走平台兜底，不返回该标记。
USER_CREDS_MISSING_CODE = "user_credentials_missing"
USER_CREDS_MISSING_HEADER = "X-Market-Error-Code"


_FIELD_LABELS = {
    "USERNAME": "账号",
    "USER": "账号",
    "ACCOUNT": "账号",
    "LOGIN": "账号",
    "PASSWORD": "密码",
    "PASSWD": "密码",
    "PWD": "密码",
    "PASS": "密码",
    "TOKEN": "Token",
    "API_KEY": "API Key",
    "APIKEY": "API Key",
    "ACCESS_KEY": "Access Key",
    "APP_KEY": "App Key",
    "APP_SECRET": "App Secret",
    "CLIENT_SECRET": "Client Secret",
    "SECRET": "密钥",
    "PRIVATE_KEY": "私钥",
    "AGENT_ID": "Agent ID",
    "ROBOT_CODE": "机器人 Code",
    "BASE_URL": "服务地址",
    "ENDPOINT": "接口地址",
    "URL": "服务地址",
    "HOST": "主机",
    "PORT": "端口",
    "DATABASE": "数据库",
    "DB_NAME": "数据库",
    "NAME": "名称",
    "TASK_FILE": "任务文件",
    "REGION": "区域",
}

_USER_KEY_RE = re.compile(r"(^|_)(USERNAME|USER|ACCOUNT|LOGIN|ACCOUNT_NAME)$", re.I)
_PASS_KEY_RE = re.compile(r"(^|_)(PASSWORD|PASSWD|PWD|PASS)$", re.I)
_TOKEN_KEY_RE = re.compile(
    r"(^|_)(TOKEN|API_KEY|APIKEY|ACCESS_KEY|APP_KEY|APP_SECRET|CLIENT_SECRET|SECRET|PRIVATE_KEY)$",
    re.I,
)


def _schema_env_keys(cap) -> list[str]:
    """只从 input_schema 读取环境变量名，避免与 declared_env_keys 递归。"""
    schema = getattr(cap, "input_schema", None) or {}
    keys: list[str] = []
    if isinstance(schema, dict):
        required = schema.get("required_env")
        if isinstance(required, list):
            keys.extend(str(k) for k in required if str(k).strip())
        hint = schema.get("env")
        if isinstance(hint, dict):
            keys.extend(str(k) for k in hint if str(k).strip())
    return sorted(dict.fromkeys(keys))


def _friendly_field_label(key: str) -> str:
    raw = str(key or "")
    upper = raw.upper()
    for suffix, label in _FIELD_LABELS.items():
        if upper == suffix or upper.endswith("_" + suffix):
            return label
    return raw


def derive_auth_methods(cap) -> list[dict]:
    """按环境变量名推导账号密码 / Token 认证方式。

    只在能力没有显式 config_ui.auth_methods 时启用；扫码授权类能力直接跳过。
    其他非凭据字段（地址、端口、任务文件等）作为公共字段放进每种方式，避免
    再以散落的 env 列表展示。
    """
    ui = getattr(cap, "config_ui", None) or {}
    if isinstance(ui, dict) and (
        ui.get("auth") or ui.get("provider") or ui.get("hide_env_fields")
    ):
        return []
    keys = _schema_env_keys(cap)
    if not keys:
        return []

    user_keys = [k for k in keys if _USER_KEY_RE.search(k)]
    pass_keys = [k for k in keys if _PASS_KEY_RE.search(k)]
    token_keys = [k for k in keys if _TOKEN_KEY_RE.search(k)]

    groups: list[tuple[str, str, list[str]]] = []
    if user_keys and pass_keys:
        groups.append(("password", "账号密码", user_keys + pass_keys))
    if token_keys:
        app_cred = any(
            k.upper().endswith("APP_KEY") or k.upper().endswith("APP_SECRET")
            for k in token_keys
        )
        groups.append(("token", "应用凭据" if app_cred else "API Token", token_keys))
    if not groups:
        return []

    method_keys = {k for _, _, group in groups for k in group}
    common_keys = [k for k in keys if k not in method_keys]
    methods: list[dict] = []
    for method_id, label, group_keys in groups:
        fields = [
            {"env": key, "label": _friendly_field_label(key)}
            for key in group_keys + common_keys
        ]
        methods.append({"id": method_id, "label": label, "fields": fields})
    return methods


def auth_methods(cap) -> list[dict]:
    """能力声明的认证方式；没有显式声明时按 env 键推导账号密码 / Token。"""
    ui = getattr(cap, "config_ui", None) or {}
    methods = ui.get("auth_methods") if isinstance(ui, dict) else None
    if isinstance(methods, list):
        explicit = [m for m in methods if isinstance(m, dict)]
        if explicit:
            return explicit
    return derive_auth_methods(cap)


def effective_config_ui(cap) -> dict:
    """返回给前端的 config_ui：补齐推导出的 auth_methods。"""
    ui = dict(getattr(cap, "config_ui", None) or {})
    if not ui.get("auth_methods"):
        methods = derive_auth_methods(cap)
        if methods:
            ui["auth_methods"] = methods
    return ui


def _method_env_keys(method: dict) -> list[str]:
    keys: list[str] = []
    for field in method.get("fields") or []:
        if isinstance(field, str):
            key = field
        elif isinstance(field, dict):
            key = field.get("env") or field.get("key")
        else:
            continue
        if key and str(key).strip() not in keys:
            keys.append(str(key).strip())
    return keys


def _auth_method_keys(cap) -> list[str]:
    keys: list[str] = []
    for method in auth_methods(cap):
        for key in _method_env_keys(method):
            if key not in keys:
                keys.append(key)
    return keys


def select_method_env(cap, env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """从已存值里选一种完整的认证方式；任一种完整即可，不要求全部字段。"""
    methods = auth_methods(cap)
    if not methods:
        return env, []
    first_missing: list[str] = []
    for method in methods:
        keys = _method_env_keys(method)
        if not keys:
            continue
        missing = [k for k in keys if not str(env.get(k) or "").strip()]
        if not missing:
            return {k: env[k] for k in keys}, []
        if not first_missing:
            first_missing = missing
    return {}, first_missing


def is_user_bound(cap) -> bool:
    """能力是否按提问者身份执行（binding=user / user&service）；默认 service（平台身份）。"""
    return (getattr(cap, "binding", None) or "service") in ("user", "user&service")


def is_user_only(cap) -> bool:
    """能力是否仅允许用户身份执行（binding=user，不提供平台兜底）。

    平台身份（服务令牌：管理员兜底重试/系统任务）一律拒绝；缺个人凭据也不发
    管理员重试标记（无平台可兜底）。
    """
    return (getattr(cap, "binding", None) or "service") == "user"


def is_service_identity(user) -> bool:
    """调用者是否服务令牌身份（纯平台身份，无个人维度）。

    使用场景：没有市场用户令牌的系统任务以服务令牌调用 runtime。服务令牌的
    market 用户必须是 ``role=admin`` 才允许使用平台凭据。
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
    env = await resolve_user_env(
        db, user_id, capability_name=cap.name, capability_id=cap.id, keys=keys
    )
    env = {k: v for k, v in env.items() if (v or "").strip()}
    selected, missing = select_method_env(cap, env)
    if auth_methods(cap):
        return selected, missing
    return env, [k for k in keys if k not in env]


def _platform_env_for(
    keys: list[str], platform: dict[str, str] | None, cap=None
) -> tuple[dict[str, str], list[str]]:
    """按声明键从平台密钥中取完整集合；空值视为缺失。"""
    plat = platform or {}
    env = {}
    for key in keys:
        value = str(plat.get(key) or "").strip()
        if value:
            env[key] = value
    if cap is not None and auth_methods(cap):
        return select_method_env(cap, env)
    return env, [key for key in keys if key not in env]


async def resolve_user_bound_env_for(
    db: AsyncSession,
    cap,
    user,
    *,
    platform_env: dict[str, str] | None = None,
) -> tuple[dict[str, str], list[str]]:
    """user 级能力的执行凭据（个人优先，market 管理员可用平台兜底）。

    - **用户令牌** → 先取个人凭据；完整则直接用。个人缺失且用户是 market
      ``role=admin`` 时，整体改用平台凭据（不与个人值混拼，避免半套凭据）；
      普通用户缺个人凭据时返回 missing，由调用方 fail-closed。
    - **服务令牌**（平台身份）→ 仅当其 market 用户为 admin 时用平台凭据；
      非管理员服务身份一律缺失（不提供平台越权）。
    - ``user`` 能力永不使用平台兜底。
    """
    keys = declared_env_keys(cap)
    if not keys:
        return {}, []
    platform = (
        platform_env
        if platform_env is not None
        else await resolve_capability_env(db, cap.name)
    )
    if is_service_identity(user):
        if is_user_only(cap) or getattr(user, "role", "") != "admin":
            return {}, keys
        return _platform_env_for(keys, platform, cap)
    env, missing = await resolve_user_bound_env(db, user.id, cap)
    if not missing:
        return env, []
    if is_user_only(cap) or getattr(user, "role", "") != "admin":
        return env, missing
    # market 管理员个人凭据不完整：整体使用平台凭据，避免个人/平台混拼。
    return _platform_env_for(keys, platform, cap)


async def require_user_bound_env(
    db: AsyncSession,
    cap,
    user,
    platform_env: dict[str, str] | None = None,
) -> dict[str, str]:
    """user 级能力执行凭据（fail-closed 校验版）。

    - ``user``：平台身份（服务令牌）一律 403；缺个人凭据也不提供平台兜底。
    - ``user`` + 用户令牌：个人凭据优先；缺失且 market 用户是 admin 时用平台凭据；
      普通用户缺失 → 403 + ``X-Market-Error-Code: user_credentials_missing``。
    - ``user`` + 服务令牌：仅 market 用户为 admin 时可用平台凭据，否则 403。
    """
    if is_user_only(cap) and is_service_identity(user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "该能力仅支持按用户身份执行（不提供平台兜底）；请使用个人凭据调用",
        )
    if is_service_identity(user) and getattr(user, "role", "") != "admin":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "当前服务身份不是能力市场管理员，不能使用平台凭据",
        )
    if platform_env is None:
        platform_env = await resolve_capability_env(db, cap.name)
    env, missing = await resolve_user_bound_env_for(db, cap, user, platform_env=platform_env)
    if missing:
        what = "、".join(missing)
        if is_user_only(cap):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"该能力仅支持按用户身份执行，缺少个人凭据：{what}；"
                "请在能力市场该能力卡片上点击「配置凭据」填写后重试",
            )
        if is_service_identity(user) or getattr(user, "role", "") == "admin":
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"该能力按平台身份执行，缺少平台密钥：{what}；"
                "请管理员在能力详情中配置平台密钥后重试",
            )
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"该能力按你的身份执行，缺少必需凭据：{what}；"
            "请在能力市场该能力卡片上点击「配置凭据」填写后重试",
            headers={USER_CREDS_MISSING_HEADER: USER_CREDS_MISSING_CODE},
        )
    return env
