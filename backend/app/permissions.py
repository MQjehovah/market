"""README 4.4 权限矩阵：

操作             Admin   Publisher   User
浏览能力          ✓        ✓          ✓
搜索能力          ✓        ✓          ✓
使用能力          ✓        ✓          ✓
发布能力          ✓        ✓          ✗
审核能力          ✓        ✗          ✗
下架能力          ✓        ✗          ✗
管理用户          ✓        ✗          ✗
查看统计          ✓        ✓(自有)     ✗
"""

from functools import wraps
from typing import Callable

from fastapi import HTTPException, status

from app.auth import CurrentUser
from app.models import Capability, User


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
    """可见性控制：private 仅作者；team 仅同团队；internal/public 全员可见。"""
    if capability.visibility == "public" or capability.visibility == "internal":
        return True
    if user is None:
        return False
    if user.role == "admin":
        return True
    if capability.visibility == "private":
        return capability.author_id == user.id
    if capability.visibility == "team":
        return capability.author.team == user.team and user.team != ""
    return False


def can_use(capability: Capability, user: User | None) -> bool:
    """正式版/预发布才可使用；草稿仅作者。"""
    if capability.status not in ("published", "deprecated", "reviewing"):
        return False
    return can_view(capability, user)
