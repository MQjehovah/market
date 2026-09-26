"""服务令牌（M2M）：创建 / 校验 / 吊销 / scope 门禁。"""

from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import hash_password
from app.models import ServiceToken, User

TOKEN_PREFIX = "mkt_svc_"
ALLOWED_SCOPES = frozenset({"runtime", "gateway", "sync", "admin"})
logger = logging.getLogger("market.service_tokens")


def hash_service_token(raw: str) -> str:
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def generate_service_token() -> str:
    return f"{TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


async def create_service_token(
    db: AsyncSession,
    *,
    admin: User,
    name: str,
    scopes: list[str] | None = None,
    expires_days: int | None = 365,
    user_id: str | None = None,
) -> tuple[ServiceToken, str]:
    cleaned = [s.strip() for s in (scopes or ["runtime", "gateway", "sync"]) if s.strip()]
    bad = [s for s in cleaned if s not in ALLOWED_SCOPES]
    if bad:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"未知 scope: {', '.join(bad)}")
    if not cleaned:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "至少选择一个 scope")

    if user_id:
        svc_user = await db.get(User, user_id)
        if svc_user is None or not svc_user.is_active:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "绑定用户不存在或已禁用")
    else:
        slug = "".join(c if c.isalnum() or c in "._-" else "_" for c in name.lower())[:40] or "bot"
        username = f"svc_{slug}"
        existing = await db.scalar(select(User).where(User.username == username))
        if existing is None:
            svc_user = User(
                username=username,
                email=f"{username}@service.local",
                password_hash=hash_password(secrets.token_urlsafe(32)),
                display_name=name,
                role="user",
                department=admin.department or "",
                is_active=True,
            )
            db.add(svc_user)
            await db.flush()
        else:
            svc_user = existing

    raw = generate_service_token()
    expires_at = None
    if expires_days:
        expires_at = datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=expires_days)

    row = ServiceToken(
        name=name.strip(),
        token_prefix=raw[:12],
        token_hash=hash_service_token(raw),
        user_id=svc_user.id,
        scopes=cleaned,
        expires_at=expires_at,
        created_by=admin.id,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row, raw


async def resolve_service_token(db: AsyncSession, raw: str) -> User | None:
    """校验 Bearer 是否为有效服务令牌；成功返回绑定用户并刷新 last_used_at。"""
    if not raw or not raw.startswith(TOKEN_PREFIX):
        return None
    digest = hash_service_token(raw)
    row = await db.scalar(select(ServiceToken).where(ServiceToken.token_hash == digest))
    if row is None or row.revoked:
        return None
    if row.expires_at is not None and row.expires_at < datetime.now(timezone.utc).replace(tzinfo=None):
        return None
    user = await db.get(User, row.user_id)
    if user is None or not user.is_active:
        return None
    row.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    # 把 scopes 挂到用户对象上，供调用方可选检查（不入库）
    setattr(user, "_service_token_scopes", list(row.scopes or []))
    setattr(user, "_service_token_id", row.id)
    setattr(user, "_service_token_prefix", row.token_prefix)
    return user


def is_service_token(user: User) -> bool:
    """当前身份是否来自服务令牌（resolve_service_token 挂载的标记属性）。"""
    return getattr(user, "_service_token_scopes", None) is not None


def require_service_scope(user: User, *scopes: str) -> None:
    """服务令牌 scope 门禁：仅在确认调用方是服务令牌后调用。

    - 非服务令牌（无 ``_service_token_scopes`` 属性/为 None）→ 403；
    - 服务令牌但 scopes 为空列表（存量令牌，早于 scope 强制）→ 放行 + 告警；
    - scopes 非空且与要求无交集 → 403（文案含所需 scope）。
    """
    granted = getattr(user, "_service_token_scopes", None)
    if granted is None:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该操作仅服务令牌可访问")
    granted = [str(s).strip() for s in granted if str(s).strip()]
    if not granted:
        logger.warning(
            "服务令牌 %s（绑定用户 %s）未配置 scopes，按存量令牌放行；本次要求 %s",
            getattr(user, "_service_token_prefix", "") or getattr(user, "_service_token_id", ""),
            user.username,
            "/".join(scopes),
        )
        return
    required = {s for s in scopes if s}
    if not required.intersection(granted):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"服务令牌缺少所需 scope：{'/'.join(sorted(required))}（当前：{', '.join(granted)}）",
        )


async def list_service_tokens(db: AsyncSession) -> list[ServiceToken]:
    return list(
        (
            await db.scalars(select(ServiceToken).order_by(ServiceToken.created_at.desc()))
        ).all()
    )


async def revoke_service_token(db: AsyncSession, token_id: str) -> ServiceToken:
    row = await db.get(ServiceToken, token_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "服务令牌不存在")
    row.revoked = True
    await db.commit()
    await db.refresh(row)
    return row
