"""管理后台：审核队列 / 上架与下架 / 用户管理 / 统计。"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import delete, func, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, DbSession
from app.auth import hash_password
from app.models import (
    A2ATask,
    AgentBinding,
    Capability,
    Notification,
    Rating,
    Review,
    ServiceToken,
    Subscription,
    UsageEvent,
    User,
    UserCapability,
    WorkflowExecution,
)
from app.permissions import require_admin
from app.schemas import (
    CapabilityOut,
    MessageOut,
    ReviewRequest,
    ServiceTokenCreate,
    ServiceTokenCreated,
    ServiceTokenOut,
    StatsOut,
    UserAdminCreate,
    UserAdminUpdate,
    UserOut,
)
from app.services.capabilities import change_status, parse_semver, review_capability
from app.services.capabilities import to_capability_out
from app.services.service_tokens import (
    create_service_token,
    list_service_tokens,
    revoke_service_token,
)
from app.services.stats import build_stats

router = APIRouter(prefix="/api/admin", tags=["admin"])


@router.get("/capabilities", response_model=list[CapabilityOut])
async def list_capabilities(db: DbSession, user: CurrentUser, status_filter: str = "", type_filter: str = ""):
    require_admin(user)
    stmt = select(Capability).options(
        selectinload(Capability.artifacts), joinedload(Capability.author)
    )
    if status_filter:
        stmt = stmt.where(Capability.status == status_filter)
    if type_filter:
        stmt = stmt.where(Capability.type == type_filter)
    caps = (await db.scalars(stmt.order_by(Capability.created_at.desc()))).all()
    if not status_filter:
        # 能力管理列表：每个逻辑能力只显示当前版本（优先最新已发布）
        grouped: dict[tuple[str, str], list[Capability]] = {}
        for cap in caps:
            grouped.setdefault((cap.name, cap.type), []).append(cap)
        caps = []
        for versions in grouped.values():
            published = [c for c in versions if c.status == "published"]
            pick = max(published, key=lambda c: parse_semver(c.version)) if published else max(
                versions, key=lambda c: parse_semver(c.version)
            )
            caps.append(pick)
        caps.sort(key=lambda c: c.created_at, reverse=True)
    out = []
    for cap in caps:
        out.append(to_capability_out(cap))
    return out


@router.post("/capabilities/{cap_id}/review", response_model=CapabilityOut)
async def review(cap_id: str, data: ReviewRequest, db: DbSession, user: CurrentUser):
    require_admin(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    cap = await review_capability(db, cap, user, data.action, data.comment)
    return to_capability_out(cap)


@router.post("/capabilities/{cap_id}/deprecate", response_model=CapabilityOut)
async def deprecate(cap_id: str, db: DbSession, user: CurrentUser):
    require_admin(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    cap = await change_status(db, cap, "deprecated")
    return to_capability_out(cap)


@router.post("/capabilities/{cap_id}/archive", response_model=CapabilityOut)
async def archive(cap_id: str, db: DbSession, user: CurrentUser):
    require_admin(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    cap = await change_status(db, cap, "archived")
    return to_capability_out(cap)


@router.get("/users", response_model=list[UserOut])
async def list_users(db: DbSession, user: CurrentUser):
    require_admin(user)
    users = (await db.scalars(select(User).order_by(User.created_at.desc()))).all()
    return [UserOut.model_validate(u) for u in users]


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: str, data: UserAdminUpdate, db: DbSession, user: CurrentUser):
    require_admin(user)
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    # 自我保护：不能禁用/降级自己
    if target.id == user.id:
        if data.is_active is False:
            raise HTTPException(status.HTTP_409_CONFLICT, "不能禁用当前登录的管理员账号")
        if data.role is not None and data.role != "admin":
            raise HTTPException(status.HTTP_409_CONFLICT, "不能把自己的管理员角色降级")
    # 至少保留一个可用管理员
    if target.role == "admin" and target.is_active:
        will_demote = data.role is not None and data.role != "admin"
        will_disable = data.is_active is False
        if will_demote or will_disable:
            active_admins = (
                await db.scalar(
                    select(func.count(User.id)).where(
                        User.role == "admin",
                        User.is_active.is_(True),
                        User.id != user_id,
                    )
                )
            ) or 0
            if active_admins == 0:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    "系统至少需要保留一个可用管理员，请先提升其他用户为管理员",
                )
    if data.username is not None and data.username != target.username:
        exists = await db.scalar(
            select(User.id).where(User.username == data.username, User.id != user_id)
        )
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, "用户名已被使用")
        target.username = data.username
    if data.email is not None and data.email != target.email:
        exists = await db.scalar(
            select(User.id).where(User.email == data.email, User.id != user_id)
        )
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, "邮箱已被使用")
        target.email = data.email
    if data.role is not None:
        target.role = data.role
    if data.is_active is not None:
        target.is_active = data.is_active
    if data.display_name is not None:
        target.display_name = data.display_name
    if data.organization is not None:
        target.organization = data.organization
    if data.team is not None:
        target.team = data.team
    if data.department is not None:
        target.department = data.department
    if data.password:
        target.password_hash = hash_password(data.password)
    await db.commit()
    await db.refresh(target)
    return UserOut.model_validate(target)


@router.delete("/users/{user_id}", response_model=MessageOut)
async def delete_user(user_id: str, db: DbSession, user: CurrentUser):
    """删除用户：其发布的能力自动转移给当前管理员，再删除账号。"""
    require_admin(user)
    if user_id == user.id:
        raise HTTPException(status.HTTP_409_CONFLICT, "不能删除当前登录的管理员账号")
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    authored = (
        await db.scalars(select(Capability).where(Capability.author_id == user_id))
    ).all()
    for cap in authored:
        cap.author_id = user.id
    await db.execute(delete(UserCapability).where(UserCapability.user_id == user_id))
    await db.execute(delete(Subscription).where(Subscription.user_id == user_id))
    await db.execute(delete(Rating).where(Rating.user_id == user_id))
    await db.execute(delete(Notification).where(Notification.user_id == user_id))
    await db.execute(delete(UsageEvent).where(UsageEvent.user_id == user_id))
    await db.execute(delete(ServiceToken).where(ServiceToken.user_id == user_id))
    await db.execute(delete(ServiceToken).where(ServiceToken.created_by == user_id))
    await db.execute(delete(AgentBinding).where(AgentBinding.created_by == user_id))
    await db.execute(delete(A2ATask).where(A2ATask.created_by == user_id))
    await db.execute(delete(WorkflowExecution).where(WorkflowExecution.created_by == user_id))
    await db.execute(delete(Review).where(Review.reviewer_id == user_id))
    await db.delete(target)
    await db.commit()
    return MessageOut(
        message=f"用户 {target.username} 已删除，其 {len(authored)} 个能力已转移给当前管理员"
    )


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(data: UserAdminCreate, db: DbSession, user: CurrentUser):
    """管理员新增用户（可指定角色）。"""
    require_admin(user)
    exists = await db.scalar(
        select(User.id).where(or_(User.username == data.username, User.email == data.email))
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, "用户名或邮箱已被使用")
    new_user = User(
        username=data.username,
        email=data.email,
        password_hash=hash_password(data.password),
        display_name=data.display_name or data.username,
        organization=data.organization,
        team=data.team,
        department=data.department,
        role=data.role,
    )
    db.add(new_user)
    await db.commit()
    await db.refresh(new_user)
    return UserOut.model_validate(new_user)


@router.get("/stats", response_model=StatsOut)
async def global_stats(db: DbSession, user: CurrentUser):
    require_admin(user)
    return await build_stats(db, user, scope="all")


@router.get("/stats/own", response_model=StatsOut)
async def own_stats(db: DbSession, user: CurrentUser):
    if user.role == "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "普通用户无权查看统计")
    return await build_stats(db, user, scope="own")


@router.get("/service-tokens", response_model=list[ServiceTokenOut])
async def admin_list_service_tokens(db: DbSession, user: CurrentUser):
    require_admin(user)
    rows = await list_service_tokens(db)
    user_ids = {r.user_id for r in rows}
    names: dict[str, str] = {}
    if user_ids:
        for u in (await db.scalars(select(User).where(User.id.in_(user_ids)))).all():
            names[u.id] = u.username
    return [
        ServiceTokenOut(
            id=r.id,
            name=r.name,
            token_prefix=r.token_prefix,
            user_id=r.user_id,
            username=names.get(r.user_id, ""),
            scopes=list(r.scopes or []),
            expires_at=r.expires_at,
            last_used_at=r.last_used_at,
            revoked=r.revoked,
            created_by=r.created_by,
            created_at=r.created_at,
        )
        for r in rows
    ]


@router.post(
    "/service-tokens",
    response_model=ServiceTokenCreated,
    status_code=status.HTTP_201_CREATED,
)
async def admin_create_service_token(
    data: ServiceTokenCreate, db: DbSession, user: CurrentUser
):
    require_admin(user)
    row, raw = await create_service_token(
        db,
        admin=user,
        name=data.name,
        scopes=data.scopes,
        expires_days=data.expires_days,
        user_id=data.user_id,
    )
    svc = await db.get(User, row.user_id)
    return ServiceTokenCreated(
        id=row.id,
        name=row.name,
        token_prefix=row.token_prefix,
        user_id=row.user_id,
        username=svc.username if svc else "",
        scopes=list(row.scopes or []),
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked=row.revoked,
        created_by=row.created_by,
        created_at=row.created_at,
        token=raw,
    )


@router.post("/service-tokens/{token_id}/revoke", response_model=ServiceTokenOut)
async def admin_revoke_service_token(token_id: str, db: DbSession, user: CurrentUser):
    require_admin(user)
    row = await revoke_service_token(db, token_id)
    svc = await db.get(User, row.user_id)
    return ServiceTokenOut(
        id=row.id,
        name=row.name,
        token_prefix=row.token_prefix,
        user_id=row.user_id,
        username=svc.username if svc else "",
        scopes=list(row.scopes or []),
        expires_at=row.expires_at,
        last_used_at=row.last_used_at,
        revoked=row.revoked,
        created_by=row.created_by,
        created_at=row.created_at,
    )
