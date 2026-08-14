"""执行引擎 API 的服务层：实例化 Agent / 调用工具 / 激活技能 / 安装 MCP，并记录用量。"""

import io
import json
import zipfile
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models import Capability, UsageEvent, User
from app.services.capabilities import get_visible_capabilities, parse_semver
from app.storage import get_storage


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
    # 原子递增，避免并发读改写
    await db.execute(
        update(Capability)
        .where(Capability.id == cap.id)
        .values(usage_count=Capability.usage_count + 1)
    )
    db.add(
        UsageEvent(
            user_id=user.id,
            capability_id=cap.id,
            action=action,
            params=params or {},
            result_status=result_status,
        )
    )


async def instantiate_agent(
    db: AsyncSession,
    user: User,
    cap: Capability,
    task: str,
    *,
    binding: "AgentBinding | None" = None,
    adhoc: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    await record_usage(db, user, cap, "instantiate", {"task": task})
    if adhoc:
        runtime = await _resolve_manifest(db, user, adhoc)
    elif binding is not None:
        runtime = await _resolve_manifest(db, user, list(binding.dependencies or []))
    else:
        runtime = await _resolve_runtime(db, user, cap)
    return {
        "instance_id": f"{cap.name}-{cap.version}-{user.id[:8]}",
        "agent": cap.name,
        "version": cap.version,
        "role": cap.description[:500] or cap.name,
        "task": task,
        "status": "ready",
        "runtime": runtime,
        "binding": binding.name if binding else ("adhoc" if adhoc else None),
        "note": "模拟实例化：真实环境中将从市场加载 Agent 配置目录并初始化会话。",
    }


async def _resolve_runtime(
    db: AsyncSession, user: User | None, cap: Capability
) -> dict[str, list[dict[str, Any]]]:
    """运行时组装：解析 agent 包 dependencies.json，返回工具/技能/MCP 的已发布版本清单。"""
    if not cap.artifacts:
        return {"tools": [], "skills": [], "mcps": []}
    try:
        content = get_storage().open(cap.artifacts[-1].uri).read()
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            if "dependencies.json" not in zf.namelist():
                return {"tools": [], "skills": [], "mcps": []}
            manifest = json.loads(zf.read("dependencies.json").decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {"tools": [], "skills": [], "mcps": []}
    return await _resolve_manifest(db, user, manifest)


async def _resolve_manifest(
    db: AsyncSession,
    user: User | None,
    manifest: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    """按依赖清单解析工具/技能/MCP 的最新或锁定版本（动态绑定的运行时组装）。"""
    runtime: dict[str, list[dict[str, Any]]] = {"tools": [], "skills": [], "mcps": []}

    visible = await get_visible_capabilities(db, user)
    for dep in manifest:
        matches = [
            c
            for c in visible
            if c.name == dep.get("name")
            and c.type == dep.get("type")
            and c.status in ("published", "deprecated")
        ]
        if not matches:
            continue
        dep_cap = max(matches, key=lambda c: parse_semver(c.version))
        item: dict[str, Any] = {"name": dep_cap.name, "version": dep_cap.version}
        if dep_cap.type == "tool":
            item["schema"] = dep_cap.input_schema or {}
        bucket = f"{dep_cap.type}s"
        if bucket in runtime:
            runtime[bucket].append(item)
    return runtime


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
