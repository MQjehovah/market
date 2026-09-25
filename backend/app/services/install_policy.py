"""安装策略：optional / default_on / required 在「我的能力」中的生效逻辑。"""

import logging

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models import Capability, User, UserCapability
from app.services.access import accessible_connected_ids, capability_access_ok
from app.services.visibility import is_capability_visible

logger = logging.getLogger("market.install_policy")


async def ensure_default_on_joins(db: AsyncSession, user: User) -> int:
    """把已发布的 default_on / required 能力自动加入当前用户的「我的能力」。返回新增条数。

    - default_on：默认加入（用户可移除）；
    - required：组织级必装（UI 不可移除），同样自动加入以保证可用；
    「已加入」按能力名判定（订阅跨版本：重发布后不重复加入新版本行）。
    """
    caps = (
        await db.scalars(
            select(Capability)
            .options(joinedload(Capability.author), selectinload(Capability.artifacts))
            .where(
                and_(
                    Capability.status == "published",
                    Capability.install_policy.in_(("default_on", "required")),
                )
            )
        )
    ).all()
    if not caps:
        return 0

    rows = (
        await db.execute(
            select(UserCapability.capability_id, Capability.name)
            .join(Capability, Capability.id == UserCapability.capability_id)
            .where(UserCapability.user_id == user.id)
        )
    ).all()
    existing = {cid for cid, _name in rows}
    existing_names = {name for _cid, name in rows}
    added = 0
    for cap in caps:
        if cap.name in existing_names:
            continue
        if not is_capability_visible(cap, user):
            logger.info("default_on 跳过能力「%s」：用户 %s 不可见", cap.name, user.username)
            continue
        if not capability_access_ok(cap, user):
            logger.info("default_on 跳过能力「%s」：用户 %s 无访问权限", cap.name, user.username)
            continue
        # plugin 组件 / agent 依赖：与手动「加入我的能力」共用连带过滤，无权限的跳过
        ids, skipped = await accessible_connected_ids(db, user, cap)
        if skipped:
            logger.info(
                "default_on 跳过能力「%s」的 %d 个连带项（不可见或无访问权限）",
                cap.name,
                skipped,
            )
        pending = [cid for cid in ids if cid not in existing]
        names_by_id: dict[str, str] = {}
        if pending:
            names_by_id = {
                cid: name
                for cid, name in (
                    await db.execute(
                        select(Capability.id, Capability.name).where(Capability.id.in_(pending))
                    )
                ).all()
            }
        for cid in ids:
            name = names_by_id.get(cid)
            if cid in existing or (name and name in existing_names):
                continue
            db.add(UserCapability(user_id=user.id, capability_id=cid))
            existing.add(cid)
            if name:
                existing_names.add(name)
            added += 1
    if added:
        await db.commit()
    return added


def is_required_policy(cap: Capability | None) -> bool:
    return bool(cap is not None and (cap.install_policy or "optional") == "required")
