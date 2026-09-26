"""README 4.4 权限矩阵：

操作                  Admin   User
浏览能力               ✓        ✓
搜索能力               ✓        ✓
使用能力（调用/执行）  ✓        ✗    （外部调用需管理员授权，见 runtime_access_roles）
发布能力               ✓        ✓
审核能力               ✓        ✗
下架能力               ✓        ✗
管理用户               ✓        ✗
查看统计               ✓        ✗
"""

from functools import wraps
from typing import Callable

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import CurrentUser
from app.config import get_settings
from app.models import Capability, User, UserCapability
from app.services.access import access_deny_reason, capability_access_ok
from app.services.visibility import is_capability_visible

# 统一 RBAC: 角色 → 权限键(与 agent/market 共享同一词汇)。``*`` 为通配。
# - capability.publish: 发布/编辑能力(所有登录用户均可)
# - capability.invoke:  执行能力(默认 Admin; 可由 runtime_access_roles 追加角色)
# - admin.*:            管理面(沿用 role=admin)
ROLE_PERMISSIONS: dict[str, set[str]] = {
    "admin": {"*"},
    "user": {"capability.publish"},
}


def role_has_permission(role: str, perm: str, *, granted_roles: set[str] | None = None) -> bool:
    """角色是否拥有权限键 ``perm``。

    ``granted_roles`` 为额外被授予该权限的角色集合(如配置 ``runtime_access_roles``
    视为被授予 ``capability.invoke``), 用于把历史配置统一收敛到权限判定。
    """
    perms = ROLE_PERMISSIONS.get(role or "", set())
    if "*" in perms or perm in perms:
        return True
    return bool(granted_roles) and role in granted_roles



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
    """路由内联管理员校验（比装饰器更易用于依赖注入风格）。统一走 RBAC 权限判定。"""
    if not role_has_permission(user.role, "*"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要管理员权限")


def _enforce_scope(user: User, cap: Capability, runtime_roles: set[str]) -> None:
    """SCOPE_ENFORCE 开启时按 scope 收窄（默认关闭，灰度迁移用）。

    调用点均已在现有准入之后，故只对**写/高危类** scope 做提升要求（只读不额外要求），
    避免误伤已订阅用户的只读使用。
    """
    from app.services.scopes import elevated_missing_scopes, scope_enforced

    if not scope_enforced():
        return
    missing = elevated_missing_scopes(user.role, cap, runtime_roles=runtime_roles)
    if missing:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"需要额外授权(step-up): {', '.join(sorted(missing))}",
        )


async def require_runtime_access(user: User, cap: Capability, db: AsyncSession) -> None:
    """执行类接口的授权门禁。

    满足任一条件即可调用：
    1. 角色拥有 ``capability.invoke`` 权限（默认 Admin;含 runtime_access_roles 追加角色）；
    2. 能力作者本人（自己创建的能力）；
    3. 已把该能力加入「我的能力」且通过统一访问谓词（部门/角色/用户白名单）。
    """
    roles = {
        r.strip()
        for r in get_settings().runtime_access_roles.split(",")
        if r.strip()
    }
    if role_has_permission(user.role, "capability.invoke", granted_roles=roles) or cap.author_id == user.id:
        _enforce_scope(user, cap, roles)
        return
    policy = cap.access_policy or "open"
    if policy == "admin_only":
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "该能力仅限管理员调用（access_policy=admin_only）",
        )
    # 订阅跟随能力名跨版本：重发布产生新行后，旧订阅行仍应放行；
    # 权限谓词（部门/角色/白名单）仍按当前解析到的行判定。
    joined = await db.scalar(
        select(UserCapability.id)
        .join(Capability, Capability.id == UserCapability.capability_id)
        .where(
            and_(
                UserCapability.user_id == user.id,
                Capability.name == cap.name,
            )
        )
    )
    if joined is not None:
        if not capability_access_ok(cap, user):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"没有调用该能力的权限（{access_deny_reason(cap, user)}）",
            )
        _enforce_scope(user, cap, roles)
        return
    raise HTTPException(
        status.HTTP_403_FORBIDDEN,
        "没有调用该能力的权限：仅管理员、能力作者或已加入「我的能力」的调用方可用",
    )


async def require_runtime_access_obo(
    db: AsyncSession, actor: User, subject: User | None, cap: Capability
) -> None:
    """代授权(on-behalf-of)执行门禁：有效权限 = actor ∩ subject。

    - actor(服务身份, 如零号员工) 必须有权执行；
    - subject(真实提问者) 若与 actor 不同, 还必须对该能力可见且通过统一访问谓词；
    - 任一不满足即拒绝。subject 为空或等于 actor 时退化为既有 ``require_runtime_access``。

    与 sync/relay 的「身份切换」不同, 这里保留 actor 门禁形成交集, 防止服务身份被
    用来放大 subject 之外的权限。
    """
    await require_runtime_access(actor, cap, db)
    if subject is None or subject.id == actor.id:
        return
    if not is_capability_visible(cap, subject):
        # 不泄露能力是否存在: 与 resolve_capability 的不可见语义一致
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在或无权访问")
    await require_runtime_access(subject, cap, db)
