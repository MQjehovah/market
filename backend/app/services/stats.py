"""统计服务：管理员全局统计 / 发布者自有统计。"""

from collections import Counter
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Capability, UsageEvent, User
from app.schemas import StatsOut
from app.services.capabilities import get_visible_capabilities


async def build_stats(db: AsyncSession, user: User, scope: str = "all") -> StatsOut:
    if scope == "own":
        caps = (await db.scalars(select(Capability).where(Capability.author_id == user.id))).all()
    else:
        caps = await get_visible_capabilities(db, user)

    cap_ids = [c.id for c in caps]
    total_users = 0
    if scope == "all" and user.role == "admin":
        total_users = (await db.scalar(select(func.count()).select_from(User))) or 0

    type_breakdown: dict[str, int] = Counter(c.type for c in caps)
    published_count = sum(1 for c in caps if c.status == "published")
    reviewing_count = sum(1 for c in caps if c.status == "reviewing")

    if cap_ids:
        usage_rows = (
            await db.execute(
                select(
                    UsageEvent.capability_id,
                    UsageEvent.action,
                    UsageEvent.created_at,
                    UsageEvent.user_id,
                ).where(UsageEvent.capability_id.in_(cap_ids))
            )
        ).all()
    else:
        usage_rows = []

    total_usage = len(usage_rows)
    by_cap: Counter[str] = Counter(r[0] for r in usage_rows)
    by_action: Counter[str] = Counter(r[1] for r in usage_rows)

    cap_by_id = {c.id: c for c in caps}
    top_used: list[dict[str, Any]] = []
    for cap_id, count in by_cap.most_common(5):
        cap = cap_by_id.get(cap_id)
        if cap:
            top_used.append(
                {
                    "id": cap.id,
                    "name": cap.name,
                    "version": cap.version,
                    "type": cap.type,
                    "count": count,
                }
            )

    recent_usage: list[dict[str, Any]] = []
    for row in sorted(usage_rows, key=lambda r: r[2], reverse=True)[:10]:
        cap = cap_by_id.get(row[0])
        if not cap:
            continue
        recent_usage.append(
            {
                "capability": f"{cap.name} v{cap.version}",
                "action": row[1],
                "at": row[2].isoformat(),
            }
        )

    return StatsOut(
        total_capabilities=len(caps),
        published_count=published_count,
        reviewing_count=reviewing_count,
        total_usage=total_usage,
        total_users=total_users,
        type_breakdown=dict(type_breakdown),
        top_used=top_used,
        recent_usage=recent_usage,
    )
