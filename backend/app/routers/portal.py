"""门户：浏览 / 搜索 / 详情 / 评分 / 订阅 / 通知 / 版本列表。"""

from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import String, and_, case, cast, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, DbSession, OptionalUser
from app.models import Capability, Notification, Rating, Subscription, User
from app.schemas import (
    AccessPolicyUpdate,
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
from app.storage import get_storage

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
    skill: str = "",
    mcp: str = "",
    include_components: bool = False,
    sort: str = "latest",
    page: int = Query(1, ge=1),
    page_size: int = Query(24, ge=1, le=100),
):
    """浏览市场：默认只展示已发布能力，且每个逻辑能力只保留最新版本。

    skill / mcp：按 Agent 内嵌能力名筛选（匹配 input_schema.embedded_*）。
    include_components：为 false 时隐藏全部 plugin 拆出子能力（含已发布）；
    仅通过 plugin 详情进入子组件，避免与独立 skill/mcp 列表重复。
    """
    conditions: list = []
    visibility_where = _visibility_where(user)
    if visibility_where is not None:
        conditions.append(visibility_where)
    if type:
        conditions.append(Capability.type == type)
    if category:
        conditions.append(Capability.category == category)
    # 列表默认只显示已发布；显式指定状态时按指定状态过滤（如管理侧排查用）
    conditions.append(Capability.status == (status or "published"))
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

    stmt = (
        select(Capability)
        .options(joinedload(Capability.author))
        .order_by(Capability.updated_at.desc())
    )
    if where_clause is not None:
        stmt = stmt.where(where_clause)
    all_caps = list((await db.scalars(stmt)).all())

    # 默认隐藏全部 plugin-component；include_components=true 时才列出
    if not include_components:
        all_caps = [c for c in all_caps if "plugin-component" not in (c.tags or [])]

    # 每个逻辑能力（名称 + 类型）只保留最新版本
    latest: dict[tuple[str, str], Capability] = {}
    for cap in all_caps:
        key = (cap.name, cap.type)
        cur = latest.get(key)
        if cur is None or parse_semver(cap.version) > parse_semver(cur.version):
            latest[key] = cap
    caps = list(latest.values())

    def _embedded_names(cap: Capability, key: str) -> set[str]:
        schema = cap.input_schema or {}
        items = schema.get(key) or []
        return {
            str(i.get("name")).strip()
            for i in items
            if isinstance(i, dict) and i.get("name")
        }

    if skill:
        skill_q = skill.strip().lower()
        caps = [
            c
            for c in caps
            if (c.type == "skill" and c.name.lower() == skill_q)
            or any(n.lower() == skill_q for n in _embedded_names(c, "embedded_skills"))
            or (
                c.type == "plugin"
                and any(
                    isinstance(x, dict)
                    and x.get("type") == "skill"
                    and str(x.get("name", "")).lower() == skill_q
                    for x in ((c.input_schema or {}).get("components") or [])
                )
            )
        ]
    if mcp:
        mcp_q = mcp.strip().lower()
        caps = [
            c
            for c in caps
            if (c.type == "mcp" and c.name.lower() == mcp_q)
            or any(n.lower() == mcp_q for n in _embedded_names(c, "embedded_mcp"))
            or (
                c.type == "plugin"
                and any(
                    isinstance(x, dict)
                    and x.get("type") == "mcp"
                    and str(x.get("name", "")).lower() == mcp_q
                    for x in ((c.input_schema or {}).get("components") or [])
                )
            )
        ]

    if sort == "usage":
        caps.sort(key=lambda c: c.usage_count, reverse=True)
    elif sort == "rating":
        caps.sort(key=lambda c: c.avg_rating, reverse=True)
    else:
        caps.sort(key=lambda c: c.updated_at, reverse=True)

    total = len(caps)
    start = (page - 1) * page_size
    page_items = caps[start : start + page_size]

    return CapabilityPage(
        items=[_to_out(c, all_caps) for c in page_items],
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/capabilities/sync", response_model=list[dict])
async def sync_capabilities(db: DbSession, user: OptionalUser):
    """同步接口：返回各能力的最新发布版本/商业包，供 Agent 等消费者拉取目录。

    含已发布的 plugin 拆出组件（skill/mcp 等），便于其他 Agent 依赖复用。
    """
    visible = await get_visible_capabilities(db, user)
    published = [c for c in visible if c.status in ("published", "deprecated")]
    latest: dict[tuple[str, str], Capability] = {}
    for cap in published:
        key = (cap.name, cap.type)
        cur = latest.get(key)
        if cur is None or parse_semver(cap.version) > parse_semver(cur.version):
            latest[key] = cap

    items = []
    for cap in sorted(latest.values(), key=lambda c: (c.type, c.name)):
        items.append(
            {
                "name": cap.name,
                "type": cap.type,
                "version": cap.version,
                "status": cap.status,
                "category": cap.category or "",
                "description": cap.description or "",
                "tags": cap.tags or [],
                "usage_count": cap.usage_count,
                "has_artifact": bool(cap.artifacts),
                "download_url": (
                    f"/api/capabilities/{quote(cap.name, safe='')}/download"
                    f"?version={cap.version}"
                ),
            }
        )
    return items


@router.get("/capabilities/{name}/download")
async def download_capability(name: str, db: DbSession, user: OptionalUser, version: str = ""):
    """下载已发布能力包（去重 zip）。消费者按名称（可选版本）获取。"""
    visible = await get_visible_capabilities(db, user)
    matches = [c for c in visible if c.name == name and c.status in ("published", "deprecated")]
    if not matches:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"能力 {name} 不存在或未发布")
    cap = None
    if version:
        cap = next((c for c in matches if c.version == version), None)
        if cap is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"能力 {name} 不存在版本 {version}")
    else:
        cap = max(matches, key=lambda c: parse_semver(c.version))
    if not cap.artifacts:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"能力 {name} v{cap.version} 未上传能力包")
    artifact = cap.artifacts[-1]
    content = get_storage().open(artifact.uri)
    headers = {
        # 中文文件名/能力名在 HTTP 头中需 percent-encode（RFC 5987）
        "Content-Disposition": (
            f'attachment; filename="package.zip"; '
            f"filename*=UTF-8''{quote(artifact.filename, safe='')}"
        ),
        "X-Capability-Name": quote(cap.name, safe=""),
        "X-Capability-Type": cap.type,
        "X-Capability-Version": cap.version,
        "X-Capability-Checksum": artifact.checksum,
    }
    return StreamingResponse(content, media_type="application/zip", headers=headers)


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
    out = _to_out(cap, same_name)
    if cap.type == "agent":
        schema = out.input_schema or {}
        if schema.get("embedded_skills") is not None or schema.get("embedded_mcp") is not None:
            from app.services.agent_metadata import enrich_embedded_with_market

            out.input_schema = await enrich_embedded_with_market(db, schema)
    if cap.type in ("skill", "mcp"):
        from app.services.agent_metadata import find_used_by

        out.used_by = await find_used_by(db, cap)
    parent_id = (cap.input_schema or {}).get("parent_plugin_id")
    if parent_id:
        out.parent_plugin_id = str(parent_id)
    return out


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


@router.post("/capabilities/{cap_id}/access", response_model=CapabilityOut)
async def update_access_policy(
    cap_id: str, data: AccessPolicyUpdate, db: DbSession, user: CurrentUser
):
    """设置能力调用权限（运行配置，不占版本）：open / admin_only / restricted。"""
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    if user.role != "admin" and cap.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只有作者或管理员可以设置调用权限")
    if cap.status not in ("published", "deprecated", "draft", "returned", "rejected"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"当前状态（{cap.status}）不允许修改调用权限")
    cap.access_policy = data.access_policy
    cap.allowed_users = [u.strip() for u in data.allowed_users if u.strip()]
    await db.commit()
    await db.refresh(cap)
    return _to_out(cap)


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
