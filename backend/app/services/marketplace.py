"""执行引擎 API 的服务层：实例化 Agent / 调用工具 / 激活技能 / 安装 MCP，并记录用量。"""

from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models import Capability, UsageEvent, User
from app.services.capabilities import get_visible_capabilities, parse_semver


async def resolve_capability(
    db: AsyncSession, user: User | None, name: str, version: str | None = None
) -> Capability:
    stmt = select(Capability).options(
        selectinload(Capability.artifacts), joinedload(Capability.author)
    ).where(Capability.name == name)
    if version:
        stmt = stmt.where(Capability.version == version)
    caps = (await db.scalars(stmt)).all()
    if not caps:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"能力 {name} 不存在")
    visible = [c for c in caps if c.visibility in ("internal", "public") or (user and c.author_id == user.id)]
    if not visible:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"能力 {name} 不存在")
    if version:
        return visible[0]
    return max(visible, key=lambda c: parse_semver(c.version))


async def record_usage(
    db: AsyncSession,
    user: User,
    cap: Capability,
    action: str,
    params: dict[str, Any] | None = None,
    result_status: str = "ok",
) -> None:
    if cap.status not in ("published", "deprecated", "reviewing"):
        raise HTTPException(status.HTTP_409_CONFLICT, f"能力 {cap.name} 当前状态不可使用")
    cap.usage_count += 1
    db.add(
        UsageEvent(
            user_id=user.id,
            capability_id=cap.id,
            action=action,
            params=params or {},
            result_status=result_status,
        )
    )


async def instantiate_agent(db: AsyncSession, user: User, cap: Capability, task: str) -> dict[str, Any]:
    await record_usage(db, user, cap, "instantiate", {"task": task})
    return {
        "instance_id": f"{cap.name}-{cap.version}-{user.id[:8]}",
        "agent": cap.name,
        "version": cap.version,
        "role": cap.description[:500] or cap.name,
        "task": task,
        "status": "ready",
        "note": "模拟实例化：真实环境中将从市场加载 Agent 配置目录并初始化会话。",
    }


async def invoke_tool(db: AsyncSession, user: User, cap: Capability, params: dict[str, Any]) -> dict[str, Any]:
    from app.sandbox.executor import execute_tool

    result = await execute_tool(cap, params)
    await record_usage(
        db,
        user,
        cap,
        "invoke",
        {
            **params,
            "_execution": result.get("execution"),
            "_ok": result.get("ok"),
        },
        result_status="ok" if result.get("ok") else "error",
    )
    return {
        "tool": cap.name,
        "version": cap.version,
        "params": params,
        "status": "ok" if result.get("ok") else "error",
        "execution": result.get("execution"),
        "output": result.get("output"),
        "error": result.get("error"),
    }


async def activate_skill(db: AsyncSession, user: User, cap: Capability, context: str) -> dict[str, Any]:
    await record_usage(db, user, cap, "activate", {"context": context})
    return {
        "skill": cap.name,
        "version": cap.version,
        "activated": True,
        "context": context,
        "note": "技能已激活，将按 SKILL.md 定义的工作流执行。",
    }


async def install_mcp(db: AsyncSession, user: User, cap: Capability, config: dict[str, Any]) -> dict[str, Any]:
    await record_usage(db, user, cap, "install", config)
    return {
        "mcp": cap.name,
        "version": cap.version,
        "installed": True,
        "tools": ["自动发现该 MCP 提供的工具列表"],
        "connection": config or {"transport": "stdio"},
    }


async def discover_mcp(db: AsyncSession, user: User) -> list[dict[str, Any]]:
    caps = await get_visible_capabilities(db, user)
    discovered = []
    for cap in caps:
        if cap.type != "mcp" or cap.status != "published":
            continue
        discovered.append(
            {
                "name": cap.name,
                "version": cap.version,
                "description": cap.description[:200],
                "category": cap.category,
                "usage_count": cap.usage_count,
            }
        )
    return discovered
