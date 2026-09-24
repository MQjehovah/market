"""能力访问权限的唯一判定来源：部门/角色/用户白名单三门（AND 语义）。

与 visibility.py 分工：visibility 决定"能否看见"，本模块决定"能否订阅/调用"。
另提供连带能力（plugin 组件 / agent 依赖）的展开与过滤，供手动加入与 default_on 自动加入共用。
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from app.models import Capability, User
from app.services.visibility import is_capability_visible


def _norm(values) -> list[str]:
    if not isinstance(values, (list, tuple)):
        return []
    return [str(v).strip() for v in values if v is not None and str(v).strip()]


def capability_access_ok(cap: Capability, user: User | None) -> bool:
    """订阅与运行共用的权限谓词（不含 visibility/status 校验；是否已订阅由调用方判定）。"""
    if user is None:
        return False
    if user.role == "admin" or cap.author_id == user.id:
        return True
    if (cap.access_policy or "open") == "admin_only":
        return False
    depts = _norm(cap.allowed_departments)
    roles = _norm(cap.allowed_roles)
    users = _norm(cap.allowed_users)
    if not (depts or roles or users):
        return (cap.access_policy or "open") == "open"
    if depts and (user.department or "").strip() not in depts:
        return False
    if roles and user.role not in roles:
        return False
    if users and user.username not in users:
        return False
    return True


def access_deny_reason(cap: Capability, user: User | None) -> str:
    """给 403/按钮提示的人类可读原因。"""
    if user is None:
        return "请先登录"
    if (cap.access_policy or "open") == "admin_only":
        return "该能力仅限管理员"
    depts = _norm(cap.allowed_departments)
    roles = _norm(cap.allowed_roles)
    users = _norm(cap.allowed_users)
    if depts and (user.department or "").strip() not in depts:
        return f"该能力仅限部门：{', '.join(depts)}"
    if roles and user.role not in roles:
        return f"该能力仅限角色：{', '.join(roles)}"
    if users and user.username not in users:
        return "该能力仅限白名单用户"
    return "没有该能力的访问权限"


async def accessible_connected_ids(
    db: AsyncSession, user: User | None, cap: Capability
) -> tuple[list[str], int]:
    """展开能力的连带 id（plugin 组件 / agent 依赖）并过滤访问权限。

    不可见或谓词不通过的连带能力跳过；返回 (可用 id 列表（含主能力）, 跳过数)。
    「加入我的能力」与 default_on 自动加入共用，保证两条路径语义一致。
    """
    ids = [cap.id]
    if cap.type == "plugin":
        from app.services.plugins import plugin_component_ids

        ids.extend(plugin_component_ids(cap))
    elif cap.type == "agent":
        from app.services.marketplace import agent_dependency_capability_ids

        ids.extend(await agent_dependency_capability_ids(db, user, cap))
    if len(ids) == 1:
        return ids, 0

    rows = (
        await db.scalars(
            select(Capability)
            .options(joinedload(Capability.author))
            .where(Capability.id.in_(ids[1:]))
        )
    ).all()
    allowed = {cap.id}
    skipped = 0
    for row in rows:
        if not is_capability_visible(row, user) or not capability_access_ok(row, user):
            skipped += 1
            continue
        allowed.add(row.id)
    return [cid for cid in ids if cid in allowed], skipped
