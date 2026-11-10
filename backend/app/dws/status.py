"""解析 `dws auth status` 与 `profiles.json`。

DWS 1.0.62 的 `auth status` 是扁平对象，profile 明细在 profiles.json；
不同版本/登录模式字段可能是 snake_case 或 camelCase，这里统一兼容。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass
class DwsStatus:
    authenticated: bool = False
    status: str = "unauthorized"
    access_expires_at: datetime | None = None
    refresh_expires_at: datetime | None = None
    corp_id: str = ""
    corp_name: str = ""
    user_id: str = ""
    user_name: str = ""
    client_id: str = ""
    current_profile: str = ""
    profiles: list[dict[str, Any]] = field(default_factory=list)
    error: str = ""


def _clean(raw: str) -> str:
    return _ANSI.sub("", (raw or "").replace("\ufeff", "")).strip()


def _json_value(raw: str) -> Any:
    text = _clean(raw)
    if not text:
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 命令可能混入提示行：从第一个 { 或 [ 开始截取
    starts = [i for i in (text.find("{"), text.find("[")) if i >= 0]
    if not starts:
        return None
    start = min(starts)
    for end in range(len(text), start, -1):
        try:
            return json.loads(text[start:end])
        except json.JSONDecodeError:
            continue
    return None


def _pick(data: dict, *keys: str) -> Any:
    for key in keys:
        if key in data and data[key] not in (None, ""):
            return data[key]
    return None


def _parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, (int, float)):
        try:
            return datetime.fromtimestamp(float(value), tz=timezone.utc).replace(tzinfo=None)
        except (OSError, OverflowError, ValueError):
            return None
    text = str(value).strip().replace("Z", "+00:00")
    if not text:
        return None
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def _normalize_profile(row: dict, current: str) -> dict[str, Any]:
    profile = str(_pick(row, "profile", "profileId") or "")
    corp_id = str(_pick(row, "corpId", "corp_id") or "")
    user_id = str(_pick(row, "userId", "user_id") or "")
    if not profile and corp_id and user_id:
        profile = f"{corp_id}:{user_id}"
    return {
        "profile": profile,
        "corpId": corp_id,
        "corpName": str(_pick(row, "corpName", "corp_name") or ""),
        "userId": user_id,
        "userName": str(_pick(row, "userName", "user_name", "name") or ""),
        "clientId": str(_pick(row, "clientId", "client_id") or ""),
        "status": str(_pick(row, "status") or ""),
        "refreshExpAt": _pick(row, "refreshExpAt", "refresh_expires_at") or "",
        "isCurrent": bool(profile and profile == current),
    }


def parse_auth_status(raw: str, profiles_raw: str = "") -> DwsStatus:
    """解析认证状态；格式不合法时返回未授权 + error，绝不抛异常。"""
    data = _json_value(raw)
    if not isinstance(data, dict):
        return DwsStatus(error="无法解析 dws auth status 输出")

    profiles_data = _json_value(profiles_raw) if profiles_raw else None
    current = ""
    profiles: list[dict[str, Any]] = []
    if isinstance(profiles_data, dict):
        current = str(_pick(profiles_data, "currentProfile", "current_profile") or "")
        rows = profiles_data.get("profiles")
        if isinstance(rows, list):
            profiles = [_normalize_profile(row, current) for row in rows if isinstance(row, dict)]
        elif isinstance(profiles_data.get("profile"), dict):
            profiles = [_normalize_profile(profiles_data["profile"], current)]

    authenticated = bool(data.get("authenticated") or data.get("loggedIn") or data.get("logged_in"))
    corp_id = str(_pick(data, "corp_id", "corpId") or "")
    ding_user_id = str(_pick(data, "user_id", "userId") or "")
    access_exp = _parse_dt(_pick(data, "expires_at", "expiresAt", "access_expires_at"))
    refresh_exp = _parse_dt(_pick(data, "refresh_expires_at", "refreshExpAt"))
    client_id = str(_pick(data, "client_id", "clientId") or "")
    current_profile = str(_pick(data, "current_profile", "currentProfile") or current)

    matched = next(
        (p for p in profiles if current_profile and p.get("profile") == current_profile),
        None,
    )
    if matched is None and profiles:
        matched = profiles[0]
    if matched:
        corp_id = corp_id or matched.get("corpId", "")
        ding_user_id = ding_user_id or matched.get("userId", "")
        client_id = client_id or matched.get("clientId", "")
        refresh_exp = refresh_exp or _parse_dt(matched.get("refreshExpAt"))

    status = "authorized" if authenticated else "unauthorized"
    if not authenticated and refresh_exp and refresh_exp <= datetime.now(timezone.utc).replace(tzinfo=None):
        status = "expired"
    if not authenticated:
        message = str(_pick(data, "message", "error") or "")
        if message and status != "expired":
            status = "unauthorized"
    return DwsStatus(
        authenticated=authenticated,
        status=status,
        access_expires_at=access_exp,
        refresh_expires_at=refresh_exp,
        corp_id=corp_id,
        corp_name=str(_pick(data, "corp_name", "corpName") or (matched or {}).get("corpName", "")),
        user_id=ding_user_id,
        user_name=str(_pick(data, "user_name", "userName") or (matched or {}).get("userName", "")),
        client_id=client_id,
        current_profile=current_profile or (matched or {}).get("profile", ""),
        profiles=profiles,
        error=str(_pick(data, "message", "error") or "") if not authenticated else "",
    )


def status_to_account_fields(status: DwsStatus) -> dict[str, Any]:
    """映射到 DwsAccount 字段；不包含任何 token。"""
    return {
        "status": status.status,
        "profiles": status.profiles,
        "current_profile": status.current_profile,
        "corp_id": status.corp_id,
        "corp_name": status.corp_name,
        "dingtalk_user_id": status.user_id,
        "dingtalk_user_name": status.user_name,
        "client_id": status.client_id,
        "access_expires_at": status.access_expires_at,
        "refresh_expires_at": status.refresh_expires_at,
        "last_error": status.error,
    }
