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
from app.services.capabilities import get_visible_capabilities, parse_semver, split_cap_ref
from app.services.visibility import is_capability_visible
from app.storage import get_storage


async def resolve_capability(
    db: AsyncSession, user: User | None, name: str, version: str | None = None
) -> Capability:
    name, ref_ver = split_cap_ref(name)
    version = version or ref_ver
    stmt = select(Capability).options(
        selectinload(Capability.artifacts), joinedload(Capability.author)
    ).where(Capability.name == name)
    if version:
        stmt = stmt.where(Capability.version == version)
    caps = (await db.scalars(stmt)).all()
    if not caps:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"能力 {name} 不存在")
    visible = [c for c in caps if is_capability_visible(c, user)]
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
    *,
    duration_ms: int = 0,
    conversation_id: str = "",
    source: str = "platform",
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
            capability_version=cap.version or "",
            action=action,
            params=params or {},
            result_status=result_status,
            duration_ms=max(0, int(duration_ms or 0)),
            conversation_id=(conversation_id or "")[:128],
            source=source or "platform",
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


# 加入助手时一并授权的依赖 kind（与 cap install 主路径对齐；不含 workflow）
_JOINABLE_DEP_TYPES = frozenset({"skill", "mcp", "tool"})


def _extend_dep_manifest(manifest: list[dict[str, Any]], raw: Any) -> None:
    if not isinstance(raw, list):
        return
    for item in raw:
        if isinstance(item, dict) and item.get("name") and item.get("type"):
            manifest.append(
                {
                    "name": str(item["name"]),
                    "type": str(item["type"]),
                    "version": str(item.get("version") or ""),
                }
            )


def _read_agent_join_manifest(cap: Capability) -> list[dict[str, Any]]:
    """加入「我的能力」用的依赖清单：dependencies.json / agent.json + 可市场关联的内嵌项。"""
    manifest: list[dict[str, Any]] = []
    arts = list(getattr(cap, "artifacts", None) or [])
    if arts:
        try:
            content = get_storage().open(arts[-1].uri).read()
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                names = set(zf.namelist())
                if "dependencies.json" in names:
                    _extend_dep_manifest(
                        manifest, json.loads(zf.read("dependencies.json").decode("utf-8"))
                    )
                elif "agent.json" in names:
                    meta = json.loads(zf.read("agent.json").decode("utf-8"))
                    if isinstance(meta, dict):
                        _extend_dep_manifest(manifest, meta.get("dependencies"))
        except Exception:  # noqa: BLE001
            pass

    schema = cap.input_schema or {}
    if not manifest:
        _extend_dep_manifest(manifest, schema.get("dependencies"))

    for item in schema.get("embedded_skills") or []:
        if isinstance(item, dict) and item.get("name"):
            manifest.append({"name": str(item["name"]), "type": "skill", "version": ""})
    for item in schema.get("embedded_mcp") or []:
        if isinstance(item, dict) and item.get("name"):
            manifest.append({"name": str(item["name"]), "type": "mcp", "version": ""})
    return manifest


async def agent_dependency_capability_ids(
    db: AsyncSession, user: User | None, cap: Capability
) -> list[str]:
    """解析助手依赖中可单独加入「我的能力」的已发布能力 id（skill/mcp/tool）。"""
    if cap.type != "agent":
        return []
    manifest = _read_agent_join_manifest(cap)
    if not manifest:
        return []

    visible = await get_visible_capabilities(db, user)
    ids: list[str] = []
    seen: set[str] = set()
    for dep in manifest:
        dep_type = str(dep.get("type") or "")
        if dep_type not in _JOINABLE_DEP_TYPES:
            continue
        matches = [
            c
            for c in visible
            if c.name == dep.get("name")
            and c.type == dep_type
            and c.status in ("published", "deprecated")
            and c.id != cap.id
        ]
        if not matches:
            continue
        version = str(dep.get("version") or "").strip()
        if version:
            locked = [c for c in matches if c.version == version]
            dep_cap = (
                locked[0] if locked else max(matches, key=lambda c: parse_semver(c.version))
            )
        else:
            dep_cap = max(matches, key=lambda c: parse_semver(c.version))
        if dep_cap.id in seen:
            continue
        seen.add(dep_cap.id)
        ids.append(dep_cap.id)
    return ids


async def _resolve_runtime(
    db: AsyncSession, user: User | None, cap: Capability
) -> dict[str, list[dict[str, Any]]]:
    """运行时组装：解析 agent 包 dependencies.json，返回工具/技能/MCP 的已发布版本清单。

    若无 dependencies.json，回退读取 input_schema 中已关联 capability_id 的
    embedded_skills / embedded_mcp。
    """
    if not cap.artifacts:
        return {"tools": [], "skills": [], "mcps": []}
    try:
        content = get_storage().open(cap.artifacts[-1].uri).read()
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            if "dependencies.json" in zf.namelist():
                manifest = json.loads(zf.read("dependencies.json").decode("utf-8"))
                return await _resolve_manifest(db, user, manifest)
    except Exception:  # noqa: BLE001
        pass

    # 无 dependencies.json：用已固化的市场关联回退
    schema = cap.input_schema or {}
    manifest: list[dict[str, Any]] = []
    for item in schema.get("embedded_skills") or []:
        if isinstance(item, dict) and item.get("capability_id") and item.get("name"):
            manifest.append({"name": item["name"], "type": "skill"})
    for item in schema.get("embedded_mcp") or []:
        if isinstance(item, dict) and item.get("capability_id") and item.get("name"):
            manifest.append({"name": item["name"], "type": "mcp"})
    if not manifest:
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


def read_skill_md(cap: Capability, *, max_chars: int = 50000) -> str:
    """从能力包读取 SKILL.md 正文（线上网关按需注入上下文，非远程执行）。"""
    from app.services.mcp_gateway import read_package_files

    raw = read_package_files(cap).get("SKILL.md")
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace")
    return text[:max_chars] if max_chars > 0 else text


async def activate_skill(db: AsyncSession, user: User, cap: Capability, context: str) -> dict[str, Any]:
    """激活技能：返回 SKILL.md 正文供调用方注入上下文（不做远程执行）。"""
    if cap.type != "skill":
        raise ValueError(f"{cap.name} 不是 skill 能力")
    skill_md = read_skill_md(cap)
    await record_usage(
        db,
        user,
        cap,
        "activate",
        {"context": context, "skill_md_chars": len(skill_md)},
    )
    note = (
        "请按下方 SKILL.md 执行任务（线上仅下发文本，不在市场侧执行）。"
        if skill_md
        else "技能包缺少 SKILL.md，无法提供执行指引。"
    )
    return {
        "skill": cap.name,
        "version": cap.version,
        "activated": bool(skill_md),
        "context": context,
        "skill_md": skill_md,
        "note": note,
    }


def read_agent_prompt(cap: Capability, *, max_chars: int = 50000) -> str:
    """从能力包读取 PROMPT.md（桌面人设 / 模型按需注入，非远程执行）。"""
    from app.services.mcp_gateway import read_package_files

    raw = read_package_files(cap).get("PROMPT.md")
    if not raw:
        return ""
    text = raw.decode("utf-8", errors="replace")
    return text[:max_chars] if max_chars > 0 else text


async def fetch_agent_persona(db: AsyncSession, user: User, cap: Capability) -> dict[str, Any]:
    """线上拉取助手人设：返回 PROMPT.md + 可加入的依赖清单（不下载 zip、不跑任务）。"""
    if cap.type != "agent":
        raise ValueError(f"{cap.name} 不是 agent 能力")
    prompt = read_agent_prompt(cap)
    deps = [
        {
            "name": str(d.get("name") or ""),
            "type": str(d.get("type") or ""),
            "version": str(d.get("version") or ""),
        }
        for d in _read_agent_join_manifest(cap)
        if d.get("name") and d.get("type")
    ]
    await record_usage(
        db,
        user,
        cap,
        "persona",
        {"prompt_chars": len(prompt), "deps": len(deps)},
    )
    note = (
        "请将 PROMPT.md 用作本地人设；依赖 skill/mcp/tool 需另装或走线上网关。"
        if prompt
        else "助手包缺少 PROMPT.md，无法提供人设。"
    )
    return {
        "agent": cap.name,
        "version": cap.version,
        "prompt": prompt,
        "dependencies": deps,
        "note": note,
    }


async def install_mcp(db: AsyncSession, user: User, cap: Capability, config: dict[str, Any]) -> dict[str, Any]:
    """安装 MCP：真实连接（stdio / HTTP / SSE / 网关），发现并返回工具列表。"""
    await record_usage(db, user, cap, "install", config)
    from app.services.mcp_gateway import (
        load_gateway_config_by_name,
        probe_tools,
        read_package_files,
    )

    files = read_package_files(cap)
    raw = files.get("connection.json")
    try:
        conn = json.loads(raw.decode("utf-8")) if raw else {}
    except (ValueError, UnicodeDecodeError):
        conn = {}
    transport = conn.get("transport", "stdio")
    if transport == "gateway":
        cfg = await load_gateway_config_by_name(db, conn.get("server") or "")
        if cfg is None:
            return {
                "mcp": cap.name,
                "version": cap.version,
                "installed": False,
                "error": f"网关服务 {conn.get('server') or ''} 不存在",
                "tools": [],
            }
    elif transport in ("http", "streamable_http", "sse", "stdio"):
        cfg = {
            "name": cap.name,
            "transport": "streamable_http" if transport == "http" else transport,
            "url": conn.get("url", ""),
            "headers": conn.get("headers") or {},
            "command": conn.get("command", "python"),
            "args": list(conn.get("args") or []),
            "env": conn.get("env") or {},
            "cwd": conn.get("cwd", ""),
        }
    else:
        return {
            "mcp": cap.name,
            "version": cap.version,
            "installed": False,
            "error": f"暂不支持 transport={transport}",
            "tools": [],
        }
    try:
        tools = await probe_tools(cfg, files)
    except Exception as exc:  # noqa: BLE001
        from app.services.mcp_gateway import format_connect_error

        return {
            "mcp": cap.name,
            "version": cap.version,
            "installed": False,
            "error": format_connect_error(exc)[:800],
            "tools": [],
        }
    return {
        "mcp": cap.name,
        "version": cap.version,
        "installed": True,
        "transport": cfg["transport"],
        "tools": [t["name"] for t in tools],
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
