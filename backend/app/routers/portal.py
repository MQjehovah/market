"""门户：浏览 / 搜索 / 详情 / 评分 / 订阅 / 通知 / 版本列表。"""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import and_, func, select
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, DbSession, OptionalUser
from app.models import Capability, Notification, Rating, Subscription, User
from app.schemas import (
    CapabilityOut,
    MessageOut,
    NotificationOut,
    RatingCreate,
    RatingOut,
    SubscribeRequest,
)
from app.services.capabilities import get_visible_capabilities, is_latest, parse_semver, record_rating
from app.services.capabilities import to_capability_out

router = APIRouter(prefix="/api", tags=["portal"])


def _to_out(cap: Capability, versions: list[Capability] | None = None) -> CapabilityOut:
    return to_capability_out(cap, versions)


@router.get("/capabilities", response_model=list[CapabilityOut])
async def browse_capabilities(
    db: DbSession,
    user: OptionalUser,
    q: str = "",
    type: str = "",
    category: str = "",
    status: str = "",
    visibility: str = "",
    sort: str = "latest",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
):
    caps = await get_visible_capabilities(db, user)
    if q:
        ql = q.lower()
        caps = [c for c in caps if ql in c.name.lower() or ql in (c.description or "").lower() or any(ql in t.lower() for t in (c.tags or []))]
    if type:
        caps = [c for c in caps if c.type == type]
    if category:
        caps = [c for c in caps if c.category == category]
    if status:
        caps = [c for c in caps if c.status == status]
    if visibility:
        caps = [c for c in caps if c.visibility == visibility]

    caps.sort(key=lambda c: (c.updated_at or c.created_at), reverse=True)
    if sort == "usage":
        caps.sort(key=lambda c: c.usage_count, reverse=True)
    elif sort == "rating":
        caps.sort(key=lambda c: c.avg_rating, reverse=True)

    versions = list(caps)
    start = (page - 1) * page_size
    return [_to_out(c, versions) for c in caps[start : start + page_size]]


@router.get("/meta/categories", response_model=dict[str, list[str]])
async def categories(db: DbSession, user: OptionalUser):
    caps = await get_visible_capabilities(db, user)
    result: dict[str, list[str]] = {}
    for c in caps:
        if c.category:
            result.setdefault(c.type, [])
            if c.category not in result[c.type]:
                result[c.type].append(c.category)
    return result


@router.get("/capabilities/{cap_id}", response_model=CapabilityOut)
async def capability_detail(cap_id: str, db: DbSession, user: OptionalUser):
    visible = await get_visible_capabilities(db, user)
    cap = next((c for c in visible if c.id == cap_id), None)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    same_name = [c for c in visible if c.name == cap.name]
    return _to_out(cap, same_name)


@router.get("/capabilities/{cap_id}/versions", response_model=list[CapabilityOut])
async def capability_versions(cap_id: str, db: DbSession, user: OptionalUser):
    visible = await get_visible_capabilities(db, user)
    cap = next((c for c in visible if c.id == cap_id), None)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    versions = [c for c in visible if c.name == cap.name]
    versions.sort(key=lambda c: parse_semver(c.version), reverse=True)
    return [_to_out(c, versions) for c in versions]


@router.get("/capabilities/{cap_id}/ratings", response_model=list[RatingOut])
async def capability_ratings(cap_id: str, db: DbSession):
    rows = (
        await db.execute(
            select(Rating, User.username)
            .join(User, Rating.user_id == User.id)
            .where(Rating.capability_id == cap_id)
            .order_by(Rating.created_at.desc())
        )
    ).all()
    result = []
    for rating, username in rows:
        out = RatingOut.model_validate(rating)
        out.username = username
        result.append(out)
    return result


@router.post("/capabilities/{cap_id}/ratings", response_model=RatingOut, status_code=status.HTTP_201_CREATED)
async def create_rating(cap_id: str, data: RatingCreate, db: DbSession, user: CurrentUser):
    cap = await db.get(Capability, cap_id)
    if cap is None or cap.status not in ("published", "deprecated"):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在或不可评分")
    if cap.author_id == user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "不能给自己的能力评分")
    rating = await record_rating(db, user, cap, data.score, data.comment)
    out = RatingOut.model_validate(rating)
    out.username = user.username
    return out


@router.post("/subscriptions", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def subscribe(data: SubscribeRequest, db: DbSession, user: CurrentUser):
    exists = await db.scalar(
        select(Subscription.id).where(
            and_(Subscription.user_id == user.id, Subscription.capability_name == data.capability_name)
        )
    )
    if exists:
        return MessageOut(message="已订阅")
    db.add(Subscription(user_id=user.id, capability_name=data.capability_name))
    await db.commit()
    return MessageOut(message="订阅成功")


@router.delete("/subscriptions", response_model=MessageOut)
async def unsubscribe(capability_name: str, db: DbSession, user: CurrentUser):
    sub = await db.scalar(
        select(Subscription).where(
            and_(Subscription.user_id == user.id, Subscription.capability_name == capability_name)
        )
    )
    if sub:
        await db.delete(sub)
        await db.commit()
    return MessageOut(message="已取消订阅")


@router.get("/notifications", response_model=list[NotificationOut])
async def notifications(db: DbSession, user: CurrentUser):
    rows = (
        await db.scalars(
            select(Notification)
            .where(Notification.user_id == user.id)
            .order_by(Notification.created_at.desc())
            .limit(50)
        )
    ).all()
    return list(rows)


@router.post("/notifications/read-all", response_model=MessageOut)
async def read_all_notifications(db: DbSession, user: CurrentUser):
    items = (
        await db.scalars(select(Notification).where(Notification.user_id == user.id, Notification.read.is_(False)))
    ).all()
    for item in items:
        item.read = True
    await db.commit()
    return MessageOut(message="已全部标记为已读")
