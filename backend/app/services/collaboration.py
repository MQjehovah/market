"""能力协作：协作者名单（按能力名，全部版本生效）+ 编辑权判定 + 站内通知。"""

from __future__ import annotations

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import CapabilityCollaborator, Notification, User


async def is_collaborator(db: AsyncSession, cap_name: str, user_id: str) -> bool:
    row = await db.scalar(
        select(CapabilityCollaborator.id).where(
            CapabilityCollaborator.capability_name == (cap_name or "").strip(),
            CapabilityCollaborator.user_id == user_id,
        )
    )
    return row is not None


async def list_collaborators(db: AsyncSession, cap_name: str) -> list[dict]:
    rows = (
        await db.scalars(
            select(CapabilityCollaborator)
            .where(CapabilityCollaborator.capability_name == (cap_name or "").strip())
            .order_by(CapabilityCollaborator.created_at)
        )
    ).all()
    if not rows:
        return []
    users = (
        await db.scalars(select(User).where(User.id.in_([r.user_id for r in rows])))
    ).all()
    umap = {u.id: u for u in users}
    out: list[dict] = []
    for r in rows:
        u = umap.get(r.user_id)
        out.append(
            {
                "user_id": r.user_id,
                "username": u.username if u else "",
                "name": (u.name or u.username) if u else "",
                "department": u.department if u else "",
                "created_at": r.created_at.isoformat() if r.created_at else "",
            }
        )
    return out


async def notify(db: AsyncSession, user_id: str, title: str, body: str = "", link: str = "") -> None:
    if not user_id:
        return
    db.add(Notification(user_id=user_id, title=title[:255], body=body, link=link))


async def add_collaborator(
    db: AsyncSession, cap_name: str, target: User, added_by: User, link: str = ""
) -> CapabilityCollaborator:
    existing = await db.scalar(
        select(CapabilityCollaborator).where(
            CapabilityCollaborator.capability_name == cap_name,
            CapabilityCollaborator.user_id == target.id,
        )
    )
    if existing is not None:
        return existing
    row = CapabilityCollaborator(
        capability_name=cap_name, user_id=target.id, added_by=added_by.id
    )
    db.add(row)
    await notify(
        db,
        target.id,
        f"你已成为能力「{cap_name}」的协作者",
        f"{added_by.name or added_by.username} 邀请你协作维护该能力（可编辑内容、开新版、提交审核）",
        link,
    )
    return row


async def remove_collaborator(db: AsyncSession, cap_name: str, target_id: str) -> bool:
    r = await db.execute(
        delete(CapabilityCollaborator).where(
            CapabilityCollaborator.capability_name == cap_name,
            CapabilityCollaborator.user_id == target_id,
        )
    )
    return bool(r.rowcount)
