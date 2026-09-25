"""官方 MCP Registry 导入（仅元数据）。

安全策略（已拍板）：
- 导入默认**仅入库元数据**：`visibility=private`、`status=draft`，不自动可执行；
- `packages`(npm/pypi/oci) 需人工批准 + 沙箱后才可执行；`remotes`(HTTP) 经网关白名单接入；
- 原始 `server.json` 与来源（registry name/status/published_at/license）存 `provenance`，
  供审核、审计与后续执行侧解析。

官方 API 形态（v0.1，`GET /v0/servers`）：
    {"servers":[{"server":{...server.json...},"_meta":{"io.modelcontextprotocol.registry/official":{...}}}],
     "metadata":{"nextCursor":"...","count":N}}
"""

from __future__ import annotations

import os
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Capability, User

REGISTRY_BASE_URL = os.getenv(
    "MCP_REGISTRY_BASE_URL", "https://registry.modelcontextprotocol.io"
).rstrip("/")
REGISTRY_SEARCH_PATH = "/v0/servers"

# 内部 name 只允许 \w.\-（见 packages.NAME_RE）：标准命名空间里的 '/' 等替换为 '_'
_INTERNAL_NAME_RE = re.compile(r"[^0-9A-Za-z_.\-]+")


def _sanitize_internal_name(slug: str) -> str:
    return _INTERNAL_NAME_RE.sub("_", slug).strip("_") or "imported"


def _pick_registry_meta(server: dict[str, Any]) -> dict[str, Any]:
    meta = server.get("_meta")
    if isinstance(meta, dict):
        official = meta.get("io.modelcontextprotocol.registry/official")
        if isinstance(official, dict):
            return official
    return {}


async def fetch_registry_servers(
    *, search: str = "", limit: int = 20, cursor: str = ""
) -> dict[str, Any]:
    """代理官方 registry 列表（管理员预览用），返回原始响应。"""
    params: dict[str, Any] = {"limit": max(1, min(int(limit), 100))}
    if search:
        params["search"] = search
    if cursor:
        params["cursor"] = cursor
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            resp = await client.get(f"{REGISTRY_BASE_URL}{REGISTRY_SEARCH_PATH}", params=params)
        resp.raise_for_status()
        data = resp.json()
    except httpx.HTTPError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, f"官方 MCP Registry 请求失败: {exc}"
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "官方 MCP Registry 响应格式异常")
    return data


def to_capability_fields(server: dict[str, Any]) -> dict[str, Any]:
    """标准 server.json → 内部能力字段（不含 author/organization/status）。"""
    name = str(server.get("name") or "").strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "server.json 缺少 name")
    version = str(server.get("version") or "0.1.0")
    remotes = server.get("remotes") if isinstance(server.get("remotes"), list) else []
    packages = server.get("packages") if isinstance(server.get("packages"), list) else []
    if remotes and not packages:
        distribution = "remote"
    elif packages and not remotes:
        distribution = "local"
    else:
        distribution = "both"
    reg = _pick_registry_meta(server)
    return {
        "name": _sanitize_internal_name(name),
        "slug": name,
        "display_name": str(server.get("title") or "").strip(),
        "description": str(server.get("description") or ""),
        "version": version,
        "type": "mcp",
        "visibility": "private",
        "distribution": distribution,
        "binding": "service",
        "install_policy": "optional",
        "access_policy": "open",
        "risk_default": "read",
        "provenance": {
            "origin": "mcp-registry",
            "registry_name": name,
            "source_url": f"{REGISTRY_BASE_URL}{REGISTRY_SEARCH_PATH}",
            "status": str(reg.get("status") or ""),
            "published_at": str(reg.get("publishedAt") or ""),
            "imported_at": datetime.now(timezone.utc).isoformat(),
            "license": str(server.get("license") or ""),
            "server_json": server,
        },
    }


async def import_server(db: AsyncSession, server: dict[str, Any], user: User) -> Capability:
    """标准 server.json → 内部能力草稿（同 name+version 幂等返回既有行）。"""
    fields = to_capability_fields(server)
    existing = await db.scalar(
        select(Capability).where(
            Capability.name == fields["name"], Capability.version == fields["version"]
        )
    )
    if existing is not None:
        return existing
    cap = Capability(
        name=fields["name"],
        display_name=fields["display_name"],
        slug=fields["slug"],
        description=fields["description"],
        version=fields["version"],
        type=fields["type"],
        visibility=fields["visibility"],
        distribution=fields["distribution"],
        binding=fields["binding"],
        install_policy=fields["install_policy"],
        access_policy=fields["access_policy"],
        risk_default=fields["risk_default"],
        provenance=fields["provenance"],
        author_id=user.id,
        organization=user.organization,
        status="draft",
    )
    db.add(cap)
    await db.commit()
    await db.refresh(cap)
    return cap
