"""门户：浏览 / 搜索 / 详情 / 评分 / 订阅 / 通知 / 版本列表。"""

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, DbSession, OptionalUser
from app.models import Capability, Notification, Rating, Subscription, User
from app.schemas import (
    CapabilityOut,
    CapabilityPage,
    MessageOut,
    NotificationOut,
    RatingCreate,
    RatingOut,
    SubscribeRequest,
)
from app.services.capabilities import get_visible_capabilities, parse_semver, record_rating
from app.services.capabilities import to_capability_out

router = APIRouter(prefix="/api", tags=["portal"])


def _to_out(cap: Capability, versions: list[Capability] | None = None) -> CapabilityOut:
    return to_capability_out(cap, versions)


def _visibility_where(user: User | None):
    """可见性过滤（SQL 层）：internal/public 全员；private 仅作者；team 仅同团队；admin 全量。"""
    if user is not None and user.role == "admin":
        return None
    clauses = [Capability.visibility.in_(["internal", "public"])]
    if user is not None:
        clauses.append(Capability.author_id == user.id)
        if user.team:
            clauses.append(
                and_(
                    Capability.visibility == "team",
                    Capability.author.has(User.team == user.team),
                )
            )
    return or_(*clauses)


@router.get("/capabilities", response_model=CapabilityPage)
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
    conditions: list = []
    visibility_where = _visibility_where(user)
    if visibility_where is not None:
        conditions.append(visibility_where)
    if type:
        conditions.append(Capability.type == type)
    if category:
        conditions.append(Capability.category == category)
    if status:
        conditions.append(Capability.status == status)
    if visibility:
        conditions.append(Capability.visibility == visibility)
    if q:
        ql = f"%{q}%"
        conditions.append(
            or_(
                Capability.name.ilike(ql),
                Capability.description.ilike(ql),
                cast(Capability.tags, String).ilike(ql),
            )
        )
    where_clause = and_(*conditions) if conditions else None

    total_stmt = select(func.count()).select_from(Capability)
    if where_clause is not None:
        total_stmt = total_stmt.where(where_clause)
    total = (await db.scalar(total_stmt)) or 0

    order_by = {
        "latest": Capability.updated_at.desc(),
        "usage": Capability.usage_count.desc(),
        "rating": case(
            (Capability.rating_count > 0, Capability.rating_sum / Capability.rating_count),
            else_=0,
        ).desc(),
    }.get(sort, Capability.updated_at.desc())

    stmt = (
        select(Capability)
        .options(joinedload(Capability.author))
        .order_by(order_by)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    if where_clause is not None:
        stmt = stmt.where(where_clause)
    caps = (await db.scalars(stmt)).all()

    return CapabilityPage(
        items=[_to_out(c) for c in caps],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/meta/categories", response_model=dict[str, list[str]])
async def categories(db: DbSession, user: OptionalUser):
    visibility_where = _visibility_where(user)
    stmt = (
        select(Capability.type, Capability.category)
        .where(Capability.category != "")
        .distinct()
    )
    if visibility_where is not None:
        stmt = stmt.where(visibility_where)
    rows = (await db.execute(stmt)).all()
    result: dict[str, list[str]] = {}
    for cap_type, cat in rows:
        result.setdefault(cap_type, []).append(cat)
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
