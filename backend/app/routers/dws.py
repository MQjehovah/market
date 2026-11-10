"""钉钉 DWS 账号、登录、工作区与审计 API。"""

from __future__ import annotations

import asyncio
import contextlib
import os
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy import select

from app.auth import CurrentUser, DbSession
from app.config import get_settings
from app.dws.accounts import account_public, get_or_create_account, refresh_account
from app.dws.device_flow import DeviceLoginManager
from app.dws.workspace import DwsWorkspace
from app.models import Capability, DwsAccount, DwsCall, UserCapability

router = APIRouter(prefix="/api/dws", tags=["dws"])

_workspace = DwsWorkspace()


def _manager() -> DeviceLoginManager:
    settings = get_settings()
    base_env: dict[str, str] = {}
    if settings.dws_client_id and settings.dws_client_secret:
        base_env["DWS_CLIENT_ID"] = settings.dws_client_id
        base_env["DWS_CLIENT_SECRET"] = settings.dws_client_secret
    return DeviceLoginManager(_workspace, settings.dws_bin, base_env=base_env)


_manager_singleton: DeviceLoginManager | None = None


def manager() -> DeviceLoginManager:
    global _manager_singleton
    if _manager_singleton is None:
        _manager_singleton = _manager()
    return _manager_singleton


async def _capability_id(db) -> str:
    row = await db.scalar(
        select(Capability)
        .where(Capability.name == "dingtalk-dws", Capability.status == "published")
        .order_by(Capability.created_at.desc())
    )
    return row.id if row else ""


async def _page_url(db) -> str:
    base = get_settings().public_base_url.rstrip("/")
    cap_id = await _capability_id(db)
    if cap_id:
        return f"{base}/capabilities/{cap_id}?tab=config"
    return f"{base}/dws"


async def _joined(db, user_id: str) -> bool:
    return bool(
        await db.scalar(
            select(UserCapability.id).where(
                UserCapability.user_id == user_id,
                UserCapability.capability_name == "dingtalk-dws",
            )
        )
    )


async def _account_payload(db, user, account: DwsAccount) -> dict[str, Any]:
    payload = account_public(account)
    payload["capability_id"] = await _capability_id(db)
    payload["page_url"] = await _page_url(db)
    payload["joined"] = await _joined(db, user.id)
    return payload


async def _logout_process(user_id: str, profile: str = "") -> None:
    settings = get_settings()
    env = dict(os.environ)
    env.update(_workspace.env(user_id))
    env.setdefault("DWS_LANG", "zh")
    env.setdefault("HOME", str(_workspace.user_dir(user_id)))
    args = [settings.dws_bin, "auth", "logout"]
    if profile:
        args.extend(["--profile", profile])
    try:
        proc = await asyncio.create_subprocess_exec(
            *args,
            cwd=str(_workspace.ensure(user_id)),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await asyncio.wait_for(proc.wait(), timeout=30)
    except Exception:  # noqa: BLE001
        pass


@router.get("/account")
async def get_account(
    user: CurrentUser,
    db: DbSession,
    refresh: bool = Query(default=False),
):
    if refresh:
        account, _ = await refresh_account(db, user.id)
        await db.commit()
    else:
        account = await get_or_create_account(db, user.id)
        await db.commit()
    return await _account_payload(db, user, account)


@router.get("/workspace")
async def get_workspace(user: CurrentUser, db: DbSession):
    account = await get_or_create_account(db, user.id)
    await db.commit()
    payload = await _account_payload(db, user, account)
    # agent 只需要关键字段，避免把登录码等短期信息带进上下文
    return {
        "user_id": user.id,
        "authorized": account.status == "authorized",
        "status": account.status,
        "page_url": payload["page_url"],
        "login_url": payload["page_url"],
        "capability_id": payload["capability_id"],
        "current_profile": account.current_profile,
    }


@router.post("/login/start")
async def start_login(user: CurrentUser, db: DbSession):
    account = await get_or_create_account(db, user.id)
    session = await manager().start(user.id)
    account.status = "authorizing" if session.status != "error" else "error"
    account.login_session_id = session.session_id
    account.login_code = session.user_code
    account.login_url = session.verification_url
    account.login_expires_at = session.expires_at
    account.last_error = session.error
    await db.commit()
    payload = await _account_payload(db, user, account)
    payload.update(session.to_public())
    payload["authorized"] = False
    return payload


@router.get("/login/{session_id}")
async def poll_login(session_id: str, user: CurrentUser, db: DbSession):
    session = await manager().poll(user.id, session_id)
    if session is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "登录会话不存在或已过期")
    account = await get_or_create_account(db, user.id)
    if session.status == "authorized":
        account, _ = await refresh_account(db, user.id)
        account.login_code = ""
        account.login_url = ""
        account.login_session_id = ""
        account.login_expires_at = None
        await db.commit()
    elif session.status in ("error", "cancelled"):
        account.status = "error" if session.status == "error" else "unauthorized"
        account.last_error = session.error
        await db.commit()
    payload = await _account_payload(db, user, account)
    payload.update(session.to_public())
    return payload


@router.post("/logout")
async def logout(
    user: CurrentUser,
    db: DbSession,
    profile: str = Query(default=""),
):
    with contextlib.suppress(Exception):
        await manager().cancel(user.id)
    await _logout_process(user.id, profile)
    account = await get_or_create_account(db, user.id)
    if profile:
        profiles = [p for p in (account.profiles or []) if p.get("profile") != profile]
        account.profiles = profiles
        if account.current_profile == profile:
            account.current_profile = ""
            account.status = "unauthorized"
    else:
        account.profiles = []
        account.current_profile = ""
        account.status = "unauthorized"
    account.login_code = ""
    account.login_url = ""
    account.login_session_id = ""
    account.login_expires_at = None
    account.last_error = ""
    await db.commit()
    return await _account_payload(db, user, account)


@router.post("/check")
async def check(user: CurrentUser, db: DbSession):
    account, _ = await refresh_account(db, user.id)
    await db.commit()
    return await _account_payload(db, user, account)


@router.get("/health")
async def health(user: CurrentUser, db: DbSession):
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅管理员可查看")
    settings = get_settings()
    version = ""
    error = ""
    try:
        proc = await asyncio.create_subprocess_exec(
            settings.dws_bin,
            "--version",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        out, err = await asyncio.wait_for(proc.communicate(), timeout=15)
        version = (out or err or b"").decode("utf-8", errors="replace").strip()[:200]
    except Exception as exc:  # noqa: BLE001
        error = f"{type(exc).__name__}: {exc}"
    writable = False
    try:
        _workspace.root.mkdir(parents=True, exist_ok=True)
        probe = _workspace.root / f".health-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink(missing_ok=True)
        writable = True
    except OSError as exc:
        error = error or str(exc)
    total = await db.scalar(select(DwsAccount.user_id).limit(1))
    return {
        "ok": bool(version and writable),
        "bin": settings.dws_bin,
        "version": version,
        "workspace_root": str(_workspace.root),
        "workspace_writable": writable,
        "error": error,
        "has_accounts": bool(total),
    }


@router.get("/calls")
async def calls(
    user: CurrentUser,
    db: DbSession,
    limit: int = Query(default=50, ge=1, le=200),
):
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅管理员可查看")
    rows = (
        await db.scalars(select(DwsCall).order_by(DwsCall.created_at.desc()).limit(limit))
    ).all()
    return [
        {
            "id": row.id,
            "user_id": row.user_id,
            "command": row.command,
            "mode": row.mode,
            "effect": row.effect,
            "status": row.status,
            "duration_ms": row.duration_ms,
            "args_summary": row.args_summary,
            "error": row.error,
            "created_at": row.created_at.isoformat() if row.created_at else "",
        }
        for row in rows
    ]
