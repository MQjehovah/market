"""服务令牌代表用户访问（X-Act-As-Sub）：把服务身份切换为目标用户视角。"""

from __future__ import annotations

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import User
from app.services.service_tokens import require_service_scope


async def resolve_act_as(
    db: AsyncSession,
    actor: User,
    sub: str | None,
    *,
    scopes: tuple[str, ...] = ("gateway", "sync"),
) -> User | None:
    """解析服务令牌要代表的用户；sub 为空返回 None（保持服务自身视角）。

    调用方需已确认 actor 是服务令牌；``scopes`` 为所需服务令牌 scope（命中任一即放行），
    默认沿用历史 gateway/sync；runtime 代授权传 ``("runtime","gateway","sync")`` 以兼容
    既有令牌（agent 平台令牌默认三 scope 齐备）。
    """
    raw = (sub or "").strip()
    if not raw:
        return None
    # 调用方（sync/relay/runtime）已按具体 scope 校验，此处兜底防误用；命中任一相关 scope 即可
    require_service_scope(actor, *scopes)
    # 精确匹配优先；回退大小写不敏感匹配。SQLite 的 lower() 仅折叠 ASCII，
    # 非 ASCII 用户名回退等价精确匹配（不会误配），够用且避免全表函数扫描。
    target = await db.scalar(select(User).where(User.username == raw))
    if target is None:
        target = await db.scalar(select(User).where(func.lower(User.username) == raw.lower()))
    if target is None or not target.is_active:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"act-as 用户不存在或已禁用: {raw}"
        )
    return target
