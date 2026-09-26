"""每账户默认开通能力：登录时把配置指定的能力自动加入「我的能力」（含连带依赖）。"""

import logging

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.config import get_settings
from app.models import Capability, User, UserCapability
from app.services.access import accessible_connected_ids, capability_access_ok
from app.services.visibility import is_capability_visible

logger = logging.getLogger("market.install_policy")


def default_capability_names() -> list[str]:
    """配置的每账户默认开通能力名（DEFAULT_CAPABILITIES，逗号分隔）。"""
    raw = get_settings().default_capabilities or ""
    return [n.strip() for n in raw.split(",") if n.strip()]


async def ensure_default_on_joins(db: AsyncSession, user: User) -> int:
    """把配置指定的能力（default_capabilities）自动加入当前用户的「我的能力」。返回新增条数。

    - 取代旧的 install_policy=default_on/required 机制：默认开通是**按账户配置**的，不是能力属性；
    - 含连带依赖（plugin 组件 / agent 依赖），无可见性/访问权限的项跳过；
    - 「已加入」按能力名判定（订阅跨版本：重发布后不重复加入新版本行）。
    """
    names = default_capability_names()
    if not names:
        return 0
    caps = (
        await db.scalars(
            select(Capability)
            .options(joinedload(Capability.author), selectinload(Capability.artifacts))
            .where(
                and_(
                    Capability.status == "published",
                    Capability.name.in_(names),
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
