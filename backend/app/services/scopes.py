"""能力 scope 目录（最小权限雏形）。

P0：定义标准 scope 键、从能力元数据推导"调用所需 scope"、角色→scope 映射；
**暂不改变现有授权判定**（`permissions.require_runtime_access` 仍按角色/订阅/白名单），
待 P2 再以 scope + step-up 替换。此模块与 `permissions.ROLE_PERMISSIONS` 对齐。

安全边界（MCP best practices）：
- 市场**不转发**调用方 Bearer 到下游（no token passthrough）；下游凭据一律由本服务自持；
- 入站 token 的 audience 强校验见 `app.core.sso_auth.verify_sso_token`。
"""

from __future__ import annotations

import os
from typing import Any

# scope 键目录（市场/运行时共用；`*` 语义由 admin/service 角色承载）
SCOPE_CATALOG: dict[str, str] = {
    "capability:read": "浏览/详情能力",
    "capability:invoke": "执行能力（工具/连接器/Agent）",
    "mcp:read": "连接器只读工具",
    "mcp:write": "连接器写工具",
    "data:read": "只读数据访问",
    "data:write": "写数据",
}

# 角色 → 基础 scope（与 ROLE_PERMISSIONS 对齐；admin/service 为全量）
_ROLE_BASE_SCOPES: dict[str, set[str]] = {
    "admin": set(SCOPE_CATALOG),
    "service": set(SCOPE_CATALOG),
    "user": {"capability:read"},
}

# 被授予运行时准入的角色（runtime_access_roles）获得的执行类 scope
_RUNTIME_GRANTED_SCOPES = {
    "capability:read", "capability:invoke", "mcp:read", "mcp:write", "data:read",
}


def capability_required_scopes(cap: Any) -> set[str]:
    """按能力类型/风险粗粒度推导"调用所需 scope"（细粒度按工具注解在运行时细分）。"""
    scopes = {"capability:invoke"}
    ctype = str(getattr(cap, "type", "") or "").lower()
    risk = str(getattr(cap, "risk_default", None) or "read").lower()
    base = "mcp" if ctype == "mcp" else "data"
    scopes.add(f"{base}:read" if risk == "read" else f"{base}:write")
    return scopes


def role_scopes(role: str, *, runtime_roles: set[str] | None = None) -> set[str]:
    """角色 → scope 集合；被 runtime_access_roles 授予的角色获得执行类 scope。"""
    scopes = set(_ROLE_BASE_SCOPES.get(role or "", {"capability:read"}))
    if role in ("admin", "service"):
        return scopes
    if runtime_roles and role in runtime_roles:
        scopes |= _RUNTIME_GRANTED_SCOPES
    return scopes


def missing_scopes(role: str, cap: Any, *, runtime_roles: set[str] | None = None) -> set[str]:
    """角色相对某能力缺失的 scope（空集=满足）。P2 用于 step-up 提示。"""
    return capability_required_scopes(cap) - role_scopes(role, runtime_roles=runtime_roles)


# 通过运行时准入后即隐含授予的 scope（只读 + 调用）：scope 收窄只针对写/高危类。
_ACCESS_IMPLIED = {"capability:invoke", "capability:read", "mcp:read", "data:read"}


def elevated_missing_scopes(role: str, cap: Any, *, runtime_roles: set[str] | None = None) -> set[str]:
    """在**已通过运行时准入**前提下，仍需提升(elevated)的 scope。

    仅写/高危类(``mcp:write``/``data:write`` 等)需要额外授权；只读调用不额外要求，
    因此开启 ``MARKET_SCOPE_ENFORCE`` 不会误伤已订阅用户的只读使用。
    """
    return capability_required_scopes(cap) - _ACCESS_IMPLIED - role_scopes(role, runtime_roles=runtime_roles)


def scope_catalog() -> list[dict[str, str]]:
    return [{"key": k, "description": v} for k, v in SCOPE_CATALOG.items()]


_TRUE = frozenset({"1", "true", "yes", "on"})


def scope_enforced() -> bool:
    """scope 强制开关（灰度）：`MARKET_SCOPE_ENFORCE=1` 时在既有授权之上按 scope 收窄。"""
    return os.getenv("MARKET_SCOPE_ENFORCE", "").strip().lower() in _TRUE
