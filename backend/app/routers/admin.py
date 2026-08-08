"""管理后台：审核队列 / 上架与下架 / 用户管理 / 统计。"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, DbSession
from app.models import Capability, User
from app.schemas import CapabilityOut, MessageOut, ReviewRequest, StatsOut, UserOut
from app.services.capabilities import change_status, review_capability
from app.services.capabilities import to_capability_out
from app.services.stats import build_stats

router = APIRouter(prefix="/api/admin", tags=["admin"])


def _require_admin(user) -> None:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅管理员可执行该操作")


@router.get("/capabilities", response_model=list[CapabilityOut])
async def list_capabilities(db: DbSession, user: CurrentUser, status_filter: str = "", type_filter: str = ""):
    _require_admin(user)
    stmt = select(Capability).options(
        selectinload(Capability.artifacts), joinedload(Capability.author)
    )
    if status_filter:
        stmt = stmt.where(Capability.status == status_filter)
    if type_filter:
        stmt = stmt.where(Capability.type == type_filter)
    caps = (await db.scalars(stmt.order_by(Capability.created_at.desc()))).all()
    out = []
    for cap in caps:
        out.append(to_capability_out(cap))
    return out


@router.post("/capabilities/{cap_id}/review", response_model=CapabilityOut)
async def review(cap_id: str, data: ReviewRequest, db: DbSession, user: CurrentUser):
    _require_admin(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    cap = await review_capability(db, cap, user, data.action, data.comment)
    return to_capability_out(cap)


@router.post("/capabilities/{cap_id}/deprecate", response_model=CapabilityOut)
async def deprecate(cap_id: str, db: DbSession, user: CurrentUser):
    _require_admin(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    cap = await change_status(db, cap, "deprecated")
    return to_capability_out(cap)


@router.post("/capabilities/{cap_id}/archive", response_model=CapabilityOut)
async def archive(cap_id: str, db: DbSession, user: CurrentUser):
    _require_admin(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    cap = await change_status(db, cap, "archived")
    return to_capability_out(cap)


@router.get("/users", response_model=list[UserOut])
async def list_users(db: DbSession, user: CurrentUser):
    _require_admin(user)
    users = (await db.scalars(select(User).order_by(User.created_at.desc()))).all()
    return [UserOut.model_validate(u) for u in users]


@router.patch("/users/{user_id}", response_model=UserOut)
async def update_user(user_id: str, payload: dict, db: DbSession, user: CurrentUser):
    _require_admin(user)
    target = await db.get(User, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "用户不存在")
    if "role" in payload:
        if payload["role"] not in ("admin", "publisher", "user"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "非法角色")
        target.role = payload["role"]
    if "is_active" in payload:
        target.is_active = bool(payload["is_active"])
    await db.commit()
    await db.refresh(target)
    return UserOut.model_validate(target)


@router.get("/stats", response_model=StatsOut)
async def global_stats(db: DbSession, user: CurrentUser):
    _require_admin(user)
    return await build_stats(db, user, scope="all")


@router.get("/stats/own", response_model=StatsOut)
async def own_stats(db: DbSession, user: CurrentUser):
    if user.role == "user":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "普通用户无权查看统计")
    return await build_stats(db, user, scope="own")
