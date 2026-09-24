"""服务令牌代表用户访问（X-Act-As-Sub）：把服务身份切换为目标用户视角。"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.service_tokens import require_service_scope


async def resolve_act_as(db: AsyncSession, actor: User, sub: str | None) -> User | None:
    """解析服务令牌要代表的用户；sub 为空返回 None（保持服务自身视角）。

    调用方需已确认 actor 是服务令牌，并在调用前细分具体 scope（sync / gateway）。
    """
    raw = (sub or "").strip()
    if not raw:
        return None
    require_service_scope(actor, "gateway", "sync")
    target = await db.scalar(select(User).where(func.lower(User.username) == raw.lower()))
    if target is None or not target.is_active:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"act-as 用户不存在或已禁用: {raw}"
        )
    return target
