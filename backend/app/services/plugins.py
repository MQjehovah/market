"""Plugin 拆包：支持 Agent Plugins / Cursor 插件目录结构。

典型目录结构：
    plugin.json                 # 或 .cursor-plugin/plugin.json
    agents/<name>/agent.json
    agents/<name>/PROMPT.md
    skills/<name>/SKILL.md      # 可选 skill.json
    mcp.json                    # Cursor 风格 mcpServers，或顶层 connection 文件
    tools/<name>/...            # 可选的 tool 子目录

上传后自动拆为 agent / skill / mcp / tool 子草稿并挂到 plugin；
审核通过时一并发布子能力。
"""

from __future__ import annotations

import io
import json
import zipfile
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Capability, CapabilityArtifact, User
from app.storage import get_storage


def _zip_bytes(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    return buf.getvalue()


def _json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")


def _read_logical_json(files: dict[str, bytes], logical: str) -> dict[str, Any]:
    raw = files.get(logical)
    if raw is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"缺少文件：{logical}")
    try:
        data = json.loads(raw.decode("utf-8-sig"))
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"文件 {logical} 不是合法 JSON：{exc}",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{logical} 必须是 JSON 对象")
    return data


def resolve_plugin_meta(files: dict[str, bytes]) -> tuple[str, dict[str, Any]]:
    for path in ("plugin.json", ".cursor-plugin/plugin.json"):
        if path in files:
            meta = _read_logical_json(files, path)
            if "name" not in meta:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"{path} 缺少 name 字段"
                )
            return path, meta
    raise HTTPException(
        status.HTTP_422_UNPROCESSABLE_ENTITY,
        "plugin 包根目录需含 plugin.json 或 .cursor-plugin/plugin.json",
    )


def _subdir_names(files: dict[str, bytes], prefix: str) -> list[str]:
    names: set[str] = set()
    p = prefix if prefix.endswith("/") else prefix + "/"
    for path in files:
        if not path.startswith(p):
            continue
        rest = path[len(p) :]
        if "/" not in rest:
            continue
        names.add(rest.split("/", 1)[0])
    return sorted(names)


def _collect_prefix(files: dict[str, bytes], prefix: str) -> dict[str, bytes]:
    p = prefix if prefix.endswith("/") else prefix + "/"
    out: dict[str, bytes] = {}
    for path, data in files.items():
        if path.startswith(p):
            out[path[len(p) :]] = data
        elif path == prefix.rstrip("/"):
            continue
    return out


def _normalize_agent_meta(raw: dict[str, Any], fallback_name: str, version: str, description: str) -> dict[str, Any]:
    """从 Agent Plugins / 平台版 agent.json 规范化为市场所需 name 等字段。"""
    identity = raw.get("identity") if isinstance(raw.get("identity"), dict) else {}
    name = (
        raw.get("name")
        or identity.get("name")
        or identity.get("display_name")
        or fallback_name
    )
    desc = (
        raw.get("description")
        or identity.get("description")
        or description
        or ""
    )
    ver = str(raw.get("version") or identity.get("version") or version)
    meta = {
        "name": str(name),
        "description": str(desc),
        "version": ver,
    }
    # 保留原生扩展字段
    for key in ("category", "tags", "skills", "subagents", "entry", "type"):
        if key in raw:
            meta[key] = raw[key]
    if identity:
        meta["identity"] = identity
    return meta


def _agent_from_dir(
    files: dict[str, bytes],
    dir_prefix: str,
    plugin: dict[str, Any],
    *,
    fallback_name: str = "",
    role: str = "agent",
) -> dict[str, Any] | None:
    """从 agents/x 或 extensions 指向的目录解析 agent 组件。"""
    base = dir_prefix if dir_prefix.endswith("/") else dir_prefix + "/"
    pkg_files = _collect_prefix(files, base)
    if not pkg_files:
        return None
    if "PROMPT.md" not in pkg_files:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{base} 缺少 PROMPT.md",
        )
    version = str(plugin.get("version") or "0.1.0")
    folder_name = fallback_name or base.strip("/").split("/")[-1] or "agent"
    if "agent.json" in pkg_files:
        try:
            loaded = json.loads(pkg_files["agent.json"].decode("utf-8-sig"))
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{base}agent.json 解析失败：{exc}",
            ) from exc
        if not isinstance(loaded, dict):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{base}agent.json 必须是对象")
        meta = _normalize_agent_meta(
            loaded, folder_name, version, str(plugin.get("description") or "")
        )
    else:
        meta = {
            "name": folder_name,
            "description": plugin.get("description", ""),
            "version": version,
        }
    pkg_files["agent.json"] = _json_bytes(meta)
    return {
        "type": "agent",
        "name": str(meta["name"]),
        "version": str(meta.get("version") or version),
        "description": str(meta.get("description") or ""),
        "role": role,
        "package": _zip_bytes(pkg_files),
    }


def _rewrite_plugin_root_paths(value: Any) -> Any:
    """将 ${PLUGIN_ROOT}/xxx 重写为相对路径 xxx。"""
    if isinstance(value, str):
        return value.replace("${PLUGIN_ROOT}/", "").replace("${PLUGIN_ROOT}", ".")
    if isinstance(value, list):
        return [_rewrite_plugin_root_paths(v) for v in value]
    if isinstance(value, dict):
        return {k: _rewrite_plugin_root_paths(v) for k, v in value.items()}
    return value


def extract_plugin_components(files: dict[str, bytes], plugin: dict[str, Any]) -> list[dict[str, Any]]:
    """从 plugin 包中抽取子能力列表（agent/skill/mcp/tool）。"""
    components: list[dict[str, Any]] = []
    version = str(plugin.get("version") or "0.1.0")
    primary = plugin.get("primary_agent") or plugin.get("main_agent") or ""

    # 1) 标准 agents/<name>/
    for agent_name in _subdir_names(files, "agents"):
        comp = _agent_from_dir(files, f"agents/{agent_name}", plugin, fallback_name=agent_name)
        if comp:
            if (primary and comp["name"] == primary) or (
                not primary and not any(c["type"] == "agent" for c in components)
            ):
                comp["role"] = "primary"
            components.append(comp)

    # 2) Agent Plugins extensions：com.xzrobot.agent → ./com.xzrobot.agent/agent.json
    extensions = plugin.get("extensions") if isinstance(plugin.get("extensions"), dict) else {}
    for ext_name, ext_cfg in extensions.items():
        if not isinstance(ext_cfg, dict):
            continue
        agent_path = str(ext_cfg.get("agent") or "").replace("\\", "/").lstrip("./")
        if not agent_path.endswith("agent.json"):
            continue
        dir_prefix = agent_path.rsplit("/", 1)[0] if "/" in agent_path else ""
        if not dir_prefix:
            continue
        if any(c["type"] == "agent" and c.get("_src") == dir_prefix for c in components):
            continue
        already = any(
            c["type"] == "agent" and c["name"] == ext_name.replace("com.", "").replace(".", "-")
            for c in components
        )
        # 已在 agents/ 下处理过的跳过
        if any(c.get("_src") == dir_prefix for c in components):
            continue
        role = "primary" if not any(c["type"] == "agent" for c in components) else "agent"
        fallback = (
            (ext_cfg.get("name") if isinstance(ext_cfg.get("name"), str) else None)
            or dir_prefix.rstrip("/").split("/")[-1]
            or ext_name
        )
        comp = _agent_from_dir(files, dir_prefix, plugin, fallback_name=str(fallback), role=role)
        if comp:
            comp["_src"] = dir_prefix
            components.append(comp)

    # 3) 任意含 agent.json + PROMPT.md 的目录（非 agents/ 前缀）
    seen_dirs = {c.get("_src") for c in components if c.get("_src")}
    seen_dirs.update(f"agents/{n}" for n in _subdir_names(files, "agents"))
    for path in files:
        if not path.endswith("/agent.json"):
            continue
        dir_prefix = path[: -len("/agent.json")]
        if dir_prefix in seen_dirs or dir_prefix.startswith("agents/"):
            continue
        sibling_prompt = f"{dir_prefix}/PROMPT.md"
        if sibling_prompt not in files:
            continue
        role = "primary" if not any(c["type"] == "agent" for c in components) else "agent"
        comp = _agent_from_dir(
            files,
            dir_prefix,
            plugin,
            fallback_name=dir_prefix.split("/")[-1],
            role=role,
        )
        if comp:
            comp["_src"] = dir_prefix
            components.append(comp)
            seen_dirs.add(dir_prefix)

    # 清理内部标记
    for c in components:
        c.pop("_src", None)

    if "agents/PROMPT.md" in files and not any(c["type"] == "agent" for c in components):
        flat = {k[len("agents/") :]: v for k, v in files.items() if k.startswith("agents/")}
        if "PROMPT.md" in flat and not _subdir_names(files, "agents"):
            meta = {
                "name": plugin.get("name", "agent"),
                "version": version,
                "description": plugin.get("description", ""),
            }
            if "agent.json" in flat:
                try:
                    loaded = json.loads(flat["agent.json"].decode("utf-8-sig"))
                    if isinstance(loaded, dict):
                        meta = _normalize_agent_meta(
                            loaded, str(plugin.get("name") or "agent"), version, str(plugin.get("description") or "")
                        )
                except Exception as exc:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY, f"agents/agent.json 解析失败：{exc}"
                    ) from exc
            else:
                flat["agent.json"] = _json_bytes(meta)
            flat["agent.json"] = _json_bytes(meta)
            components.append(
                {
                    "type": "agent",
                    "name": str(meta.get("name") or plugin["name"]),
                    "version": str(meta.get("version") or version),
                    "description": str(meta.get("description") or ""),
                    "role": "primary",
                    "package": _zip_bytes(flat),
                }
            )

    for skill_name in _subdir_names(files, "skills"):
        base = f"skills/{skill_name}/"
        pkg_files = _collect_prefix(files, base)
        if "SKILL.md" not in pkg_files:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"skills/{skill_name}/ 缺少 SKILL.md",
            )
        meta = {"name": skill_name, "description": f"来自插件 {plugin.get('name')}", "version": version}
        if "skill.json" in pkg_files:
            try:
                loaded = json.loads(pkg_files["skill.json"].decode("utf-8-sig"))
                if isinstance(loaded, dict):
                    meta = {**meta, **loaded}
                    meta.setdefault("name", skill_name)
            except Exception as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"skills/{skill_name}/skill.json 解析失败：{exc}",
                ) from exc
        else:
            pkg_files["skill.json"] = _json_bytes(meta)
        components.append(
            {
                "type": "skill",
                "name": str(meta.get("name") or skill_name),
                "version": str(meta.get("version") or version),
                "description": str(meta.get("description") or ""),
                "role": "skill",
                "package": _zip_bytes(pkg_files),
            }
        )

    for tool_name in _subdir_names(files, "tools"):
        pkg_files = _collect_prefix(files, f"tools/{tool_name}/")
        required = ("tool.json", "schema.json", "implementation/tool.py")
        missing = [r for r in required if r not in pkg_files]
        if missing:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"tools/{tool_name}/ 缺少 {', '.join(missing)}",
            )
        try:
            meta = json.loads(pkg_files["tool.json"].decode("utf-8-sig"))
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"tools/{tool_name}/tool.json 解析失败：{exc}",
            ) from exc
        if not isinstance(meta, dict):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"tools/{tool_name}/tool.json 必须是对象")
        name = str(meta.get("name") or tool_name)
        schema = json.loads(pkg_files["schema.json"].decode("utf-8-sig"))
        components.append(
            {
                "type": "tool",
                "name": name,
                "version": str(meta.get("version") or version),
                "description": str(meta.get("description") or ""),
                "role": "tool",
                "input_schema": schema if isinstance(schema, dict) else {},
                "package": _zip_bytes(pkg_files),
            }
        )

    if "mcp.json" in files:
        mcp_root = _read_logical_json(files, "mcp.json")
        servers: dict[str, Any] = {}
        if "mcpServers" in mcp_root and isinstance(mcp_root["mcpServers"], dict):
            servers = mcp_root["mcpServers"]
        elif "servers" in mcp_root and isinstance(mcp_root["servers"], dict):
            servers = mcp_root["servers"]
        elif mcp_root.get("transport") or mcp_root.get("command") or mcp_root.get("url") or mcp_root.get("type"):
            servers = {str(plugin.get("name") or "mcp"): mcp_root}
        else:
            if "connection.json" not in files:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "mcp.json 需包含 mcpServers 或已有 connection 等字段",
                )

        mcp_impl = _collect_prefix(files, "mcp/")
        for srv_name, cfg in servers.items():
            if not isinstance(cfg, dict):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"MCP {srv_name} 配置必须是对象"
                )
            connection = _rewrite_plugin_root_paths(dict(cfg))
            if "transport" not in connection:
                # Agent Plugins 常用 type: stdio
                t = connection.pop("type", None) if connection.get("type") in ("stdio", "http", "sse") else None
                if t:
                    connection["transport"] = t
                elif connection.get("url"):
                    connection["transport"] = "http"
                else:
                    connection["transport"] = "stdio"
            elif connection.get("type") in ("stdio", "http", "sse") and "transport" not in connection:
                connection["transport"] = connection.pop("type")
            # 若同时有 type 与 transport，去掉冗余 type
            if connection.get("type") in ("stdio", "http", "sse") and connection.get("transport"):
                connection.pop("type", None)

            pkg: dict[str, bytes] = {
                "mcp.json": _json_bytes(
                    {
                        "name": srv_name,
                        "description": connection.get("description")
                        or f"来自 {plugin.get('name')} 的 MCP",
                        "version": version,
                    }
                ),
                "connection.json": _json_bytes(connection),
                "tools.json": _json_bytes(connection.get("tools") or []),
                "security.json": _json_bytes(connection.get("security") or {"level": "medium"}),
            }
            for rel, data in mcp_impl.items():
                pkg[f"mcp/{rel}"] = data
            components.append(
                {
                    "type": "mcp",
                    "name": srv_name,
                    "version": version,
                    "description": str(
                        connection.get("description") or f"插件 MCP：{srv_name}"
                    ),
                    "role": "mcp",
                    "package": _zip_bytes(pkg),
                }
            )

    if (
        "connection.json" in files
        and "tools.json" in files
        and "security.json" in files
        and not any(c["type"] == "mcp" for c in components)
    ):
        meta = (
            _read_logical_json(files, "mcp.json")
            if "mcp.json" in files
            else {"name": f"{plugin['name']}-mcp", "version": version}
        )
        pkg_files = {
            k: files[k]
            for k in ("mcp.json", "connection.json", "tools.json", "security.json")
            if k in files
        }
        if "mcp.json" not in pkg_files:
            pkg_files["mcp.json"] = _json_bytes(meta)
        components.append(
            {
                "type": "mcp",
                "name": str(meta.get("name") or f"{plugin['name']}-mcp"),
                "version": str(meta.get("version") or version),
                "description": str(meta.get("description") or ""),
                "role": "mcp",
                "package": _zip_bytes(pkg_files),
            }
        )

    if not components:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "plugin 包需至少包含 agents/、extensions 指向的 Agent、skills/、tools/ 或 mcp.json 之一",
        )
    agents = [c for c in components if c["type"] == "agent"]
    if agents and not any(c.get("role") == "primary" for c in agents):
        agents[0]["role"] = "primary"
    return components


async def _upsert_component(
    db: AsyncSession,
    user: User,
    plugin_cap: Capability,
    comp: dict[str, Any],
) -> Capability:
    name = comp["name"]
    version = comp["version"]
    type_ = comp["type"]
    existing = await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts))
        .where(
            and_(
                Capability.name == name,
                Capability.version == version,
            )
        )
    )
    if existing is not None:
        if existing.author_id != user.id:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"组件 {type_}:{name} v{version} 已存在且不属于当前用户",
            )
        # 已发布/审核中的同名同版本：直接挂到 plugin，避免覆盖线上包
        if existing.status not in ("draft", "returned", "rejected"):
            if existing.type != type_:
                raise HTTPException(
                    status.HTTP_409_CONFLICT,
                    f"组件 {name} v{version} 已存在为 {existing.type}，与 plugin 中的 {type_} 冲突",
                )
            return existing
        cap = existing
        cap.type = type_
        cap.description = comp.get("description") or cap.description
        cap.visibility = plugin_cap.visibility
        cap.organization = user.organization
        cap.input_schema = comp.get("input_schema") or {}
        storage = get_storage()
        for art in list(cap.artifacts or []):
            try:
                storage.delete(art.uri)
            except Exception:
                pass
            await db.delete(art)
    else:
        cap = Capability(
            name=name,
            description=comp.get("description") or "",
            type=type_,
            version=version,
            category=plugin_cap.category or "未分类",
            tags=list(plugin_cap.tags or []) + ["plugin-component"],
            visibility=plugin_cap.visibility,
            access_policy=plugin_cap.access_policy,
            allowed_users=list(plugin_cap.allowed_users or []),
            author_id=user.id,
            organization=user.organization,
            status="draft",
            input_schema=comp.get("input_schema") or {},
        )
        db.add(cap)
        await db.flush()

    info = get_storage().save(cap.id, f"{name}-{version}.zip", io.BytesIO(comp["package"]))
    db.add(
        CapabilityArtifact(
            capability_id=cap.id,
            filename=f"{name}-{version}.zip",
            **info,
        )
    )
    return cap


async def materialize_plugin_components(
    db: AsyncSession,
    user: User,
    plugin_cap: Capability,
    content: bytes,
    details: dict[str, Any],
) -> list[dict[str, Any]]:
    """解析 plugin 包并物化/更新子能力，写入 plugin.input_schema。"""
    files_map: dict[str, str] = details["files"]
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        logical_files = {logical: zf.read(raw) for logical, raw in files_map.items()}

    plugin = details["meta"]
    extracted = extract_plugin_components(logical_files, plugin)

    old = (plugin_cap.input_schema or {}).get("components") or []
    old_ids = {c.get("capability_id") for c in old if isinstance(c, dict)}

    refs: list[dict[str, Any]] = []
    for comp in extracted:
        cap = await _upsert_component(db, user, plugin_cap, comp)
        refs.append(
            {
                "type": cap.type,
                "name": cap.name,
                "version": cap.version,
                "capability_id": cap.id,
                "role": comp.get("role") or cap.type,
            }
        )
        old_ids.discard(cap.id)

    plugin_cap.input_schema = {
        "kind": "plugin",
        "plugin": {
            "name": plugin.get("name"),
            "description": plugin.get("description", ""),
            "version": plugin.get("version", plugin_cap.version),
        },
        "components": refs,
    }
    if not plugin_cap.description and plugin.get("description"):
        plugin_cap.description = str(plugin["description"])
    await db.flush()
    return refs


def plugin_component_ids(cap: Capability) -> list[str]:
    schema = cap.input_schema or {}
    if schema.get("kind") != "plugin":
        return []
    return [
        str(c["capability_id"])
        for c in (schema.get("components") or [])
        if isinstance(c, dict) and c.get("capability_id")
    ]


async def publish_plugin_components(db: AsyncSession, plugin_cap: Capability) -> None:
    """插件审核通过时，将子草稿一并标记为 published。"""
    ids = plugin_component_ids(plugin_cap)
    if not ids:
        return
    rows = (await db.scalars(select(Capability).where(Capability.id.in_(ids)))).all()
    for row in rows:
        if row.status in ("draft", "reviewing", "returned", "rejected"):
            row.status = "published"
