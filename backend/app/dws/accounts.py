"""DWS 账号状态：调用 `dws auth status` 并同步 DwsAccount。"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.dws.status import DwsStatus, parse_auth_status, status_to_account_fields
from app.dws.workspace import DwsWorkspace
from app.models import DwsAccount

logger = logging.getLogger("market.dws.accounts")


async def get_or_create_account(db: AsyncSession, user_id: str) -> DwsAccount:
    account = await db.get(DwsAccount, user_id)
    if account is None:
        account = DwsAccount(user_id=user_id, status="unauthorized")
        db.add(account)
        await db.flush()
    return account


async def read_dws_status(
    workspace: DwsWorkspace,
    user_id: str,
    *,
    bin_path: str | None = None,
    extra_env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> DwsStatus:
    settings = get_settings()
    env = dict(os.environ)
    env.update(workspace.env(user_id))
    env.setdefault("DWS_LANG", "zh")
    env.setdefault("HOME", str(workspace.user_dir(user_id)))
    if extra_env:
        env.update(extra_env)
    try:
        proc = await asyncio.create_subprocess_exec(
            bin_path or settings.dws_bin,
            "auth",
            "status",
            "--format",
            "json",
            cwd=str(workspace.ensure(user_id)),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:  # noqa: BLE001
        return DwsStatus(status="error", error=f"无法启动 dws: {type(exc).__name__}: {exc}")

    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.wait()
        return DwsStatus(status="error", error="dws auth status 超时")
    raw = stdout.decode("utf-8", errors="replace") if stdout else ""
    err = stderr.decode("utf-8", errors="replace") if stderr else ""
    profiles_raw = ""
    profiles_path = Path(workspace.config_dir(user_id)) / "profiles.json"
    try:
        profiles_raw = profiles_path.read_text(encoding="utf-8")
    except OSError:
        pass
    status = parse_auth_status(raw, profiles_raw)
    if not status.error and err.strip() and not status.authenticated:
        status.error = err.strip()[:800]
    return status


async def refresh_account(db: AsyncSession, user_id: str) -> tuple[DwsAccount, DwsStatus]:
    settings = get_settings()
    workspace = DwsWorkspace()
    extra: dict[str, str] = {}
    if settings.dws_client_id and settings.dws_client_secret:
        extra["DWS_CLIENT_ID"] = settings.dws_client_id
        extra["DWS_CLIENT_SECRET"] = settings.dws_client_secret
    status = await read_dws_status(workspace, user_id, extra_env=extra)
    account = await get_or_create_account(db, user_id)
    for key, value in status_to_account_fields(status).items():
        setattr(account, key, value)
    if status.authenticated:
        account.last_login_at = account.last_login_at or datetime.now(timezone.utc).replace(tzinfo=None)
        account.login_code = ""
        account.login_url = ""
        account.login_session_id = ""
        account.login_expires_at = None
    await db.flush()
    return account, status


def account_public(account: DwsAccount) -> dict[str, Any]:
    return {
        "user_id": account.user_id,
        "status": account.status,
        "authorized": account.status == "authorized",
        "profiles": account.profiles or [],
        "current_profile": account.current_profile or "",
        "corp_id": account.corp_id or "",
        "corp_name": account.corp_name or "",
        "dingtalk_user_id": account.dingtalk_user_id or "",
        "dingtalk_user_name": account.dingtalk_user_name or "",
        "client_id": account.client_id or "",
        "access_expires_at": account.access_expires_at.isoformat() if account.access_expires_at else "",
        "refresh_expires_at": account.refresh_expires_at.isoformat() if account.refresh_expires_at else "",
        "last_login_at": account.last_login_at.isoformat() if account.last_login_at else "",
        "last_used_at": account.last_used_at.isoformat() if account.last_used_at else "",
        "login_session_id": account.login_session_id or "",
        "login_code": account.login_code or "",
        "login_url": account.login_url or "",
        "login_expires_at": account.login_expires_at.isoformat() if account.login_expires_at else "",
        "last_error": account.last_error or "",
    }


def profiles_json(config_dir: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads((config_dir / "profiles.json").read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    rows = data.get("profiles") if isinstance(data, dict) else None
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []
