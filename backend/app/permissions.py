"""README 4.4 权限矩阵：

操作             Admin   Publisher   User
浏览能力          ✓        ✓          ✓
搜索能力          ✓        ✓          ✓
使用能力（调用/执行） ✓      ✗          ✗    （外部调用需管理员授权，见 runtime_access_roles）
发布能力          ✓        ✓          ✗
审核能力          ✓        ✗          ✗
下架能力          ✓        ✗          ✗
管理用户          ✓        ✗          ✗
查看统计          ✓        ✓(自有)     ✗
"""

from functools import wraps
from typing import Callable

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.config import get_settings
from app.models import Capability, User, UserCapability
from app.services.visibility import is_capability_visible


def require_role(*roles: str) -> Callable:
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        async def wrapper(*args, **kwargs):
            user: User | None = None
            for value in (kwargs.get("user"), kwargs.get("current_user"), kwargs.get("me")):
                if isinstance(value, User):
                    user = value
                    break
            if user is None:
                raise HTTPException(status.HTTP_401_UNAUTHORIZED, "未登录")
            if user.role not in roles:
                raise HTTPException(status.HTTP_403_FORBIDDEN, "没有执行该操作的权限")
            return await func(*args, **kwargs)

        return wrapper

    return decorator


def can_view(capability: Capability, user: User | None) -> bool:
    """可见性控制：private 仅作者；team 仅同团队；internal/public 全员可见；admin 全量。"""
    return is_capability_visible(capability, user)


def can_use(capability: Capability, user: User | None) -> bool:
    """已发布或弃用期内才可使用（与 runtime 门禁一致；审核中不可当正式消费）。"""
    if capability.status not in ("published", "deprecated"):
        return False
    return can_view(capability, user)


def require_admin(user: User) -> None:
    """路由内联管理员校验（比装饰器更易用于依赖注入风格）。"""
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")


async def require_runtime_access(user: User, cap: Capability, db: AsyncSession) -> None:
    """执行类接口的授权门禁。

    满足任一条件即可调用：
    1. 角色在 runtime_access_roles 配置中（默认 admin）；
    2. 能力作者本人（自己创建的能力）；
    3. 已把该能力加入「我的能力」的调用方。
    """
    roles = {
        r.strip()
        for r in get_settings().runtime_access_roles.split(",")
        if r.strip()
    }
    if user.role in roles or cap.author_id == user.id:
        return
    policy = cap.access_policy or "open"
    if policy == "admin_only":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "该能力仅限管理员调用（access_policy=admin_only）",
        )
    joined = await db.scalar(
        select(UserCapability.id).where(
            and_(
                UserCapability.user_id == user.id,
                UserCapability.capability_id == cap.id,
            )
        )
    )
    if joined is not None:
        if policy == "restricted":
            allowed = [u.strip() for u in (cap.allowed_users or []) if u.strip()]
            if user.username not in allowed:
                raise HTTPException(
                    status.HTTP_403_FORBIDDEN,
                    f"该能力仅限白名单用户调用：{', '.join(allowed) or '（未配置）'}",
                )
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "没有调用该能力的权限：仅管理员、能力作者或已加入「我的能力」的调用方可用",
    )
