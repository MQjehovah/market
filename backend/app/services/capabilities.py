"""能力生命周期：草稿 → 提交审核 → 审核中 → 通过(发布)/驳回/打回 → 弃用 → 归档。"""

import re
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Capability,
    Notification,
    Rating,
    Review,
    Subscription,
    User,
)
from app.schemas import ArtifactOut, CapabilityCreate, CapabilityOut, CapabilityUpdate

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

STATUS_FLOW: dict[str, set[str]] = {
    "draft": {"reviewing"},
    "reviewing": {"published", "rejected", "returned"},
    "rejected": {"reviewing", "archived"},
    "returned": {"reviewing", "archived"},
    "published": {"deprecated", "archived"},
    "deprecated": {"archived", "published"},
    "archived": set(),
}


def parse_semver(version: str) -> tuple[int, int, int]:
    m = SEMVER_RE.match(version)
    if not m:
        raise ValueError(f"非法语义化版本号：{version}")
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def next_version(version: str, change_type: str) -> str:
    major, minor, patch = parse_semver(version)
    if change_type == "major":
        return f"{major + 1}.0.0"
    if change_type == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def is_latest(cap: Capability, versions: list[Capability]) -> bool:
    same_name = [v for v in versions if v.name == cap.name]
    if not same_name:
        return True
    return cap == max(same_name, key=lambda c: parse_semver(c.version))


def to_capability_out(
    cap: Capability,
    versions: list[Capability] | None = None,
    author_name: str | None = None,
) -> CapabilityOut:
    """安全序列化：仅读取已加载的关联，避免异步懒加载。"""
    data: dict[str, object] = {
        "id": cap.id,
        "name": cap.name,
        "description": cap.description or "",
        "type": cap.type,
        "version": cap.version,
        "status": cap.status,
        "category": cap.category or "",
        "tags": cap.tags or [],
        "visibility": cap.visibility,
        "author_id": cap.author_id,
        "organization": cap.organization or "",
        "usage_count": cap.usage_count,
        "rating_sum": cap.rating_sum,
        "rating_count": cap.rating_count,
        "avg_rating": cap.avg_rating,
        "created_at": cap.created_at,
        "updated_at": cap.updated_at,
    }
    if author_name is not None:
        data["author_name"] = author_name
    elif "author" in cap.__dict__ and cap.author is not None:
        data["author_name"] = cap.author.username
    if "artifacts" in cap.__dict__:
        data["artifacts"] = [ArtifactOut.model_validate(a) for a in cap.artifacts]
    if versions is not None:
        data["latest"] = is_latest(cap, versions)
    return CapabilityOut(**data)


async def get_visible_capabilities(
    db: AsyncSession, user: User | None, *, include_private: bool = False
) -> list[Capability]:
    stmt = select(Capability).options(
        selectinload(Capability.artifacts), joinedload(Capability.author)
    )
    if user is not None and user.role == "admin":
        result = await db.scalars(stmt)
        return list(result)
    result = await db.scalars(stmt)
    all_caps = list(result)
    visible = []
    for cap in all_caps:
        if cap.visibility in ("internal", "public"):
            visible.append(cap)
        elif user is not None and cap.author_id == user.id:
            visible.append(cap)
        elif (
            user is not None
            and cap.visibility == "team"
            and cap.author.team == user.team
            and user.team
        ):
            visible.append(cap)
    return visible


async def create_capability(
    db: AsyncSession, user: User, data: CapabilityCreate
) -> Capability:
    exists = await db.scalar(
        select(Capability.id).where(
            and_(Capability.name == data.name, Capability.version == data.version)
        )
    )
    if exists:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"能力 {data.name} 已存在版本 {data.version}"
        )
    cap = Capability(
        name=data.name,
        description=data.description,
        type=data.type,
        version=data.version,
        category=data.category,
        tags=data.tags,
        visibility=data.visibility,
        author_id=user.id,
        organization=user.organization,
        status="draft",
    )
    db.add(cap)
    await db.commit()
    await db.refresh(cap)
    return cap


async def update_capability(
    db: AsyncSession, cap: Capability, data: CapabilityUpdate
) -> Capability:
    if data.description is not None:
        cap.description = data.description
    if data.category is not None:
        cap.category = data.category
    if data.tags is not None:
        cap.tags = data.tags
    if data.visibility is not None:
        cap.visibility = data.visibility
    await db.commit()
    await db.refresh(cap)
    return cap


async def submit_for_review(db: AsyncSession, cap: Capability) -> Capability:
    _transition(cap, "reviewing")
    db.add(Review(capability_id=cap.id, reviewer_id=cap.author_id, action="submitted", comment="提交审核"))
    await db.commit()
    await db.refresh(cap)
    return cap


async def review_capability(
    db: AsyncSession,
    cap: Capability,
    reviewer: User,
    action: str,
    comment: str = "",
) -> Capability:
    if action == "take":
        if cap.status != "reviewing":
            raise HTTPException(status.HTTP_409_CONFLICT, "该能力不在待审状态")
        db.add(Review(capability_id=cap.id, reviewer_id=reviewer.id, action="reviewing", comment=comment or "开始审核"))
        await db.commit()
        return cap

    target = {"approve": "published", "reject": "rejected", "return": "returned"}.get(action)
    if target is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "未知审核动作")
    _transition(cap, target)
    db.add(Review(capability_id=cap.id, reviewer_id=reviewer.id, action=action, comment=comment))

    author = await db.get(User, cap.author_id)
    if author is not None:
        labels = {"approve": "已通过审核并发布", "reject": "被驳回", "return": "被打回修改"}
        db.add(
            Notification(
                user_id=author.id,
                title=f"能力 {cap.name} v{cap.version} {labels[action]}",
                body=comment or f"管理员已完成审核：{labels[action]}",
            )
        )

    if target == "published":
        await _notify_subscribers(db, cap)
    await db.commit()
    await db.refresh(cap)
    return cap


async def _notify_subscribers(db: AsyncSession, cap: Capability) -> None:
    subs = (await db.scalars(select(Subscription).where(Subscription.capability_name == cap.name))).all()
    for sub in subs:
        if sub.user_id == cap.author_id:
            continue
        db.add(
            Notification(
                user_id=sub.user_id,
                title=f"你订阅的能力发布新版本：{cap.name} v{cap.version}",
                body=cap.description[:200] or f"{cap.name} v{cap.version} 已上架，点击查看。",
            )
        )


async def change_status(db: AsyncSession, cap: Capability, target: str) -> Capability:
    _transition(cap, target)
    if target == "deprecated":
        db.add(
            Notification(
                user_id=cap.author_id,
                title=f"能力 {cap.name} v{cap.version} 已弃用",
                body="请迁移到新版本。",
            )
        )
    await db.commit()
    await db.refresh(cap)
    return cap


def _transition(cap: Capability, target: str) -> None:
    if target not in STATUS_FLOW.get(cap.status, set()):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"不允许从 {cap.status} 切换到 {target}",
        )
    cap.status = target


async def create_new_version(
    db: AsyncSession, user: User, cap: Capability, new_version: str
) -> Capability:
    exists = await db.scalar(
        select(Capability.id).where(
            and_(Capability.name == cap.name, Capability.version == new_version)
        )
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"版本 {new_version} 已存在")
    new_cap = Capability(
        name=cap.name,
        description=cap.description,
        type=cap.type,
        version=new_version,
        category=cap.category,
        tags=list(cap.tags or []),
        visibility=cap.visibility,
        author_id=user.id,
        organization=cap.organization,
        status="draft",
    )
    db.add(new_cap)
    await db.commit()
    await db.refresh(new_cap)
    return new_cap


async def record_rating(db: AsyncSession, user: User, cap: Capability, score: int, comment: str) -> Rating:
    existing = await db.scalar(
        select(Rating).where(and_(Rating.capability_id == cap.id, Rating.user_id == user.id))
    )
    if existing:
        cap.rating_sum -= existing.score
        existing.score = score
        existing.comment = comment
        cap.rating_sum += score
        rating = existing
    else:
        rating = Rating(capability_id=cap.id, user_id=user.id, score=score, comment=comment)
        db.add(rating)
        cap.rating_count += 1
        cap.rating_sum += score
    await db.commit()
    await db.refresh(rating)
    return rating
