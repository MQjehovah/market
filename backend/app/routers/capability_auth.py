"""通用能力认证 API：按能力 config_ui.provider 分发，不写具体能力名。"""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from sqlalchemy import select

from app.auth import CurrentUser, DbSession
from app.models import Capability, UserCapability
from app.services.capability_auth import get_auth_provider

router = APIRouter(prefix="/api/capabilities", tags=["capability-auth"])


async def _resolve(db, name: str) -> Capability:
    cap = await db.scalar(
        select(Capability)
        .where(Capability.name == name, Capability.status == "published")
        .order_by(Capability.created_at.desc())
    )
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"能力 {name} 不存在")
    return cap


def _provider(cap):
    provider = get_auth_provider(cap)
    if provider is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该能力未声明认证配置")
    return provider


async def _joined(db, user_id: str, cap) -> bool:
    return bool(
        await db.scalar(
            select(UserCapability.id).where(
                UserCapability.user_id == user_id,
                UserCapability.capability_name == cap.name,
            )
        )
    )


async def _with_joined(db, user, cap, data):
    if isinstance(data, dict):
        data["joined"] = await _joined(db, user.id, cap)
        data["capability_id"] = cap.id
    return data


@router.get("/{name}/auth/account")
async def account(
    name: str,
    user: CurrentUser,
    db: DbSession,
    refresh: bool = Query(default=False),
):
    cap = await _resolve(db, name)
    data = await _provider(cap).account(user, db, cap, refresh=refresh)
    return await _with_joined(db, user, cap, data)


@router.get("/{name}/auth/workspace")
async def workspace(name: str, user: CurrentUser, db: DbSession):
    cap = await _resolve(db, name)
    data = await _provider(cap).workspace(user, db, cap)
    return await _with_joined(db, user, cap, data)


@router.post("/{name}/auth/login/start")
async def login_start(name: str, user: CurrentUser, db: DbSession):
    cap = await _resolve(db, name)
    data = await _provider(cap).start(user, db, cap)
    return await _with_joined(db, user, cap, data)


@router.get("/{name}/auth/login/{session_id}")
async def login_poll(name: str, session_id: str, user: CurrentUser, db: DbSession):
    cap = await _resolve(db, name)
    data = await _provider(cap).poll(user, db, cap, session_id)
    return await _with_joined(db, user, cap, data)


@router.post("/{name}/auth/logout")
async def logout(
    name: str,
    user: CurrentUser,
    db: DbSession,
    profile: str = Query(default=""),
):
    cap = await _resolve(db, name)
    data = await _provider(cap).logout(user, db, cap, profile=profile)
    return await _with_joined(db, user, cap, data)


@router.post("/{name}/auth/check")
async def check(name: str, user: CurrentUser, db: DbSession):
    cap = await _resolve(db, name)
    data = await _provider(cap).check(user, db, cap)
    return await _with_joined(db, user, cap, data)


@router.get("/{name}/auth/schema")
async def schema(
    name: str,
    user: CurrentUser,
    db: DbSession,
    path: str = Query(default=""),
    query: str = Query(default=""),
):
    cap = await _resolve(db, name)
    return await _provider(cap).schema(user, db, cap, path, query)


@router.post("/{name}/auth/files")
async def upload(name: str, user: CurrentUser, db: DbSession, file: UploadFile = File(...)):
    cap = await _resolve(db, name)
    return await _provider(cap).upload(user, db, cap, file)


@router.get("/{name}/auth/files/{file_id}")
async def download(name: str, file_id: str, user: CurrentUser, db: DbSession):
    cap = await _resolve(db, name)
    return await _provider(cap).download(user, db, cap, file_id)


@router.get("/{name}/auth/health")
async def health(name: str, user: CurrentUser, db: DbSession):
    cap = await _resolve(db, name)
    return await _provider(cap).health(user, db, cap)


@router.get("/{name}/auth/calls")
async def calls(
    name: str,
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
):
    cap = await _resolve(db, name)
    return await _provider(cap).calls(user, db, cap, limit)
