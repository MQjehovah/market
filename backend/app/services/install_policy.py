"""安装策略：optional / default_on / required 在「我的能力」中的生效逻辑。"""

import logging

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Capability, User, UserCapability
from app.services.access import capability_access_ok

logger = logging.getLogger("market.install_policy")


async def ensure_default_on_joins(db: AsyncSession, user: User) -> int:
    """把已发布的 default_on 能力自动加入当前用户的「我的能力」。返回新增条数。"""
    caps = (
        await db.scalars(
            select(Capability).where(
                and_(
                    Capability.status == "published",
                    Capability.install_policy == "default_on",
                )
            )
        )
    ).all()
    if not caps:
        return 0

    existing = set(
        await db.scalars(
            select(UserCapability.capability_id).where(UserCapability.user_id == user.id)
        )
    )
    added = 0
    for cap in caps:
        if cap.id in existing:
            continue
        if not capability_access_ok(cap, user):
            logger.info(
                "default_on 跳过能力「%s」：用户 %s 无访问权限", cap.name, user.username
            )
            continue
        # plugin：一并加入组件
        ids = [cap.id]
        if cap.type == "plugin":
            from app.services.plugins import plugin_component_ids

            ids.extend(plugin_component_ids(cap))
        for cid in ids:
            if cid in existing:
                continue
            db.add(UserCapability(user_id=user.id, capability_id=cid))
            existing.add(cid)
            added += 1
    if added:
        await db.commit()
    return added


def is_required_policy(cap: Capability | None) -> bool:
    return bool(cap is not None and (cap.install_policy or "optional") == "required")
