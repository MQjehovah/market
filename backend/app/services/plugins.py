"""Plugin 拆包：支持 Agent Plugins / Cursor Plugin 目录结构。

典型目录结构：
    plugin.json 或 .cursor-plugin/plugin.json
    agents/<name>/PROMPT.md     # 市场助手；或 agents/*.md 子代理
    skills/<name>/SKILL.md
    rules/*.mdc                 # Cursor rules
    commands/*.md               # 斜杠命令
    hooks/hooks.json + scripts/
    mcp.json
    tools/<name>/...

上传后自动拆为 agent / skill / mcp / tool / rule / command / hook 子草稿；
审核通过时一并发布子能力。
"""

from __future__ import annotations

import io
import json
import re
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
        "能力包根目录需含 plugin.json 或兼容能力包清单",
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


_RULE_EXTS = (".mdc", ".md", ".markdown")
_COMMAND_EXTS = (".md", ".mdc", ".markdown", ".txt")
_AGENT_MD_EXTS = (".md", ".mdc", ".markdown")


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Parse simple YAML-like --- frontmatter; unknown structure is ignored."""
    raw = text.lstrip("\ufeff")
    if not raw.startswith("---"):
        return {}, text
    rest = raw[3:].lstrip("\r\n")
    idx = rest.find("\n---")
    if idx < 0:
        return {}, text
    block = rest[:idx]
    body = rest[idx + 4 :].lstrip("\r\n")
    meta: dict[str, Any] = {}
    for line in block.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, val = line.split(":", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        low = val.lower()
        if low in ("true", "yes"):
            meta[key] = True
        elif low in ("false", "no"):
            meta[key] = False
        elif val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            meta[key] = (
                [p.strip().strip('"').strip("'") for p in inner.split(",") if p.strip()]
                if inner
                else []
            )
        else:
            meta[key] = val
    return meta, body


def _safe_comp_name(name: str) -> str:
    s = re.sub(r"[^\w.\-]+", "-", str(name).strip(), flags=re.UNICODE).strip("-.")
    return s or "item"


def _file_stem(path: str) -> str:
    name = path.replace("\\", "/").rsplit("/", 1)[-1]
    if "." in name:
        return name.rsplit(".", 1)[0]
    return name


def _norm_plugin_path(value: str) -> str:
    return str(value).replace("\\", "/").lstrip("./").rstrip("/")


def _manifest_prefixes(plugin: dict[str, Any], field: str, default: str) -> list[str]:
    raw = plugin.get(field)
    if raw is None:
        return [default]
    if isinstance(raw, str):
        p = _norm_plugin_path(raw)
        return [p] if p else [default]
    if isinstance(raw, list):
        out = [_norm_plugin_path(str(v)) for v in raw if v]
        return out or [default]
    return [default]


def _iter_ext_files(files: dict[str, bytes], prefix: str, exts: tuple[str, ...]) -> list[str]:
    p = prefix if prefix.endswith("/") else prefix + "/"
    found: list[str] = []
    for path in files:
        if not path.startswith(p):
            continue
        lower = path.lower()
        if any(lower.endswith(ext) for ext in exts):
            found.append(path)
    return sorted(found)


def extract_plugin_components(files: dict[str, bytes], plugin: dict[str, Any]) -> list[dict[str, Any]]:
    """从 plugin 包中抽取子能力列表（agent/skill/mcp/tool/rule/command/hook）。"""
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

    existing_agent_names = {c["name"] for c in components if c["type"] == "agent"}
    existing_agent_dirs = set(_subdir_names(files, "agents"))
    for prefix in _manifest_prefixes(plugin, "agents", "agents"):
        for path in _iter_ext_files(files, prefix, _AGENT_MD_EXTS):
            rest = path[len(prefix) :].lstrip("/") if path.startswith(prefix) else path
            if "/" in rest:
                continue
            stem = _file_stem(path)
            if stem in existing_agent_dirs:
                continue
            try:
                text = files[path].decode("utf-8-sig")
            except UnicodeDecodeError:
                continue
            fm, body = parse_frontmatter(text)
            name = _safe_comp_name(str(fm.get("name") or stem))
            if name in existing_agent_names:
                continue
            desc = str(fm.get("description") or plugin.get("description") or "")
            meta = {"name": name, "description": desc, "version": version}
            pkg_files = {
                "agent.json": _json_bytes(meta),
                "PROMPT.md": (body or text).encode("utf-8"),
            }
            role = "primary" if not existing_agent_names else "agent"
            components.append(
                {
                    "type": "agent",
                    "name": name,
                    "version": version,
                    "description": desc,
                    "role": role,
                    "package": _zip_bytes(pkg_files),
                }
            )
            existing_agent_names.add(name)

    for skill_name in _subdir_names(files, "skills"):
        base = f"skills/{skill_name}/"
        pkg_files = _collect_prefix(files, base)
        if "SKILL.md" not in pkg_files:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"skills/{skill_name}/ 缺少 SKILL.md",
            )
        meta = {"name": skill_name, "description": f"来自能力包 {plugin.get('name')}", "version": version}
        if "skill.json" in pkg_files:
            try:
                loaded = json.loads(pkg_files["skill.json"].decode("utf-8-sig"))
                if isinstance(loaded, dict):
                    meta = {**meta, **loaded}
                    meta.setdefault("name", skill_name)
                    meta.setdefault("version", version)
            except Exception as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"skills/{skill_name}/skill.json 解析失败：{exc}",
                ) from exc
        else:
            pkg_files["skill.json"] = _json_bytes(meta)
        from app.services.packages import validate_skill_meta

        validate_skill_meta(meta, f"skills/{skill_name}/skill.json", require_version=True)
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

    if "SKILL.md" in files and not any(c["type"] == "skill" for c in components):
        fm, body = parse_frontmatter(files["SKILL.md"].decode("utf-8-sig", errors="replace"))
        skill_name = _safe_comp_name(str(fm.get("name") or plugin.get("name") or "skill"))
        meta = {
            "name": skill_name,
            "description": str(fm.get("description") or plugin.get("description") or ""),
            "version": version,
        }
        pkg_files = {
            "SKILL.md": (body or files["SKILL.md"].decode("utf-8-sig", errors="replace")).encode("utf-8"),
            "skill.json": _json_bytes(meta),
        }
        from app.services.packages import validate_skill_meta

        validate_skill_meta(meta, "SKILL.md", require_version=True)
        components.append(
            {
                "type": "skill",
                "name": skill_name,
                "version": version,
                "description": str(meta["description"]),
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
        try:
            schema = json.loads(pkg_files["schema.json"].decode("utf-8-sig"))
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"tools/{tool_name}/schema.json 解析失败：{exc}",
            ) from exc
        from app.services.packages import validate_tool_schema

        schema = validate_tool_schema(schema, f"tools/{tool_name}/schema.json")
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
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"连接器 {srv_name} 配置必须是对象"
                )
            connection = _rewrite_plugin_root_paths(dict(cfg))
            if "transport" not in connection:
                # Agent Plugins 常用 type: stdio
                t = connection.pop("type", None) if connection.get("type") in ("stdio", "http", "sse", "streamable-http") else None
                if t:
                    connection["transport"] = "http" if t == "streamable-http" else t
                elif connection.get("url"):
                    connection["transport"] = "http"
                else:
                    connection["transport"] = "stdio"
            elif connection.get("type") in ("stdio", "http", "sse", "streamable-http") and "transport" not in connection:
                t = connection.pop("type")
                connection["transport"] = "http" if t == "streamable-http" else t
            # 若同时有 type 与 transport，去掉冗余 type
            if connection.get("type") in ("stdio", "http", "sse", "streamable-http") and connection.get("transport"):
                connection.pop("type", None)
            if connection.get("transport") == "streamable-http":
                connection["transport"] = "http"

            from app.services.packages import validate_mcp_connection

            validate_mcp_connection(connection, f"mcp.json[{srv_name}]")

            pkg: dict[str, bytes] = {
                "mcp.json": _json_bytes(
                    {
                        "name": srv_name,
                        "description": connection.get("description")
                        or f"来自 {plugin.get('name')} 的连接器",
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
                        connection.get("description") or f"能力包连接器：{srv_name}"
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
        from app.services.packages import validate_mcp_connection

        conn = json.loads(pkg_files["connection.json"].decode("utf-8-sig"))
        if isinstance(conn, dict):
            validate_mcp_connection(conn, "connection.json")
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

    seen_rule_names = set()
    for prefix in _manifest_prefixes(plugin, "rules", "rules"):
        for path in _iter_ext_files(files, prefix, _RULE_EXTS):
            try:
                text = files[path].decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"{path} 不是合法 UTF-8：{exc}"
                ) from exc
            fm, body = parse_frontmatter(text)
            name = _safe_comp_name(str(fm.get("name") or _file_stem(path)))
            if name in seen_rule_names:
                continue
            seen_rule_names.add(name)
            desc = str(fm.get("description") or plugin.get("description") or "")
            globs = fm.get("globs") or ""
            if isinstance(globs, list):
                globs = ",".join(str(g) for g in globs)
            always = bool(fm.get("alwaysApply") or fm.get("always_apply"))
            meta = {
                "name": name,
                "description": desc,
                "version": version,
                "alwaysApply": always,
                "globs": globs,
            }
            pkg_files = {
                "rule.json": _json_bytes(meta),
                "RULE.mdc": (body or text).encode("utf-8"),
            }
            components.append(
                {
                    "type": "rule",
                    "name": name,
                    "version": version,
                    "description": desc,
                    "role": "rule",
                    "package": _zip_bytes(pkg_files),
                }
            )

    seen_cmd_names = set()
    for prefix in _manifest_prefixes(plugin, "commands", "commands"):
        for path in _iter_ext_files(files, prefix, _COMMAND_EXTS):
            try:
                text = files[path].decode("utf-8-sig")
            except UnicodeDecodeError as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"{path} 不是合法 UTF-8：{exc}"
                ) from exc
            fm, body = parse_frontmatter(text)
            name = _safe_comp_name(str(fm.get("name") or _file_stem(path)))
            if name in seen_cmd_names:
                continue
            seen_cmd_names.add(name)
            desc = str(fm.get("description") or plugin.get("description") or "")
            meta = {"name": name, "description": desc, "version": version}
            pkg_files = {
                "command.json": _json_bytes(meta),
                "COMMAND.md": (body or text).encode("utf-8"),
            }
            components.append(
                {
                    "type": "command",
                    "name": name,
                    "version": version,
                    "description": desc,
                    "role": "command",
                    "package": _zip_bytes(pkg_files),
                }
            )

    hooks_cfg: dict[str, Any] | None = None
    hooks_scripts: dict[str, bytes] = {}
    hooks_field = plugin.get("hooks")
    if isinstance(hooks_field, dict) and (
        isinstance(hooks_field.get("hooks"), dict) or hooks_field.get("version") is not None
    ):
        hooks_cfg = hooks_field
        hooks_scripts = _collect_prefix(files, "scripts")
    else:
        hook_path = "hooks/hooks.json"
        if isinstance(hooks_field, str) and hooks_field.strip():
            hook_path = _norm_plugin_path(hooks_field)
            if not hook_path.endswith(".json"):
                hook_path = f"{hook_path}/hooks.json" if hook_path else "hooks/hooks.json"
        if hook_path in files:
            try:
                loaded = json.loads(files[hook_path].decode("utf-8-sig"))
            except Exception as exc:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"{hook_path} 解析失败：{exc}"
                ) from exc
            if isinstance(loaded, dict):
                hooks_cfg = loaded
                hooks_scripts = _collect_prefix(files, "scripts")
                extra = _collect_prefix(files, "hooks/")
                for rel, data in extra.items():
                    if rel == "hooks.json":
                        continue
                    hooks_scripts.setdefault(rel, data)
    if hooks_cfg is not None:
        hook_name = _safe_comp_name(str(plugin.get("name") or "plugin") + "-hooks")
        events = hooks_cfg.get("hooks") if isinstance(hooks_cfg.get("hooks"), dict) else {}
        desc = f"来自能力包 {plugin.get('name')} 的 Hooks"
        if isinstance(events, dict) and events:
            desc = f"{desc}（{', '.join(list(events.keys())[:6])}）"
        meta = {"name": hook_name, "description": desc, "version": version}
        pkg_files = {
            "hook.json": _json_bytes(meta),
            "hooks.json": _json_bytes(hooks_cfg),
        }
        for rel, data in hooks_scripts.items():
            pkg_files[f"scripts/{rel}"] = data
        components.append(
            {
                "type": "hook",
                "name": hook_name,
                "version": version,
                "description": desc,
                "role": "hook",
                "package": _zip_bytes(pkg_files),
            }
        )

    if not components:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "plugin 包需至少包含 agents/、skills/、rules/、commands/、hooks/、tools/ 或 mcp.json 之一",
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
    parent_schema = {"parent_plugin_id": plugin_cap.id}
    if comp.get("input_schema"):
        parent_schema = {**comp["input_schema"], **parent_schema}

    # 名称归属：组件名可能是包内自带的新 name，须防抢注他人名称建行；
    # 名称规范化：去首尾空白（包内 skill.json 等可携带空白名）
    from app.services.capabilities import ensure_name_ownership, normalize_cap_name

    name = normalize_cap_name(name)
    await ensure_name_ownership(db, name, user)

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
            schema = dict(existing.input_schema or {})
            schema["parent_plugin_id"] = plugin_cap.id
            existing.input_schema = schema
            return existing
        cap = existing
        cap.type = type_
        cap.description = comp.get("description") or cap.description
        cap.visibility = plugin_cap.visibility
        cap.organization = user.department
        tags = list(cap.tags or [])
        if "plugin-component" not in tags:
            tags.append("plugin-component")
        cap.tags = tags
        cap.input_schema = parent_schema
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
            allowed_departments=list(plugin_cap.allowed_departments or []),
            allowed_roles=list(plugin_cap.allowed_roles or []),
            author_id=user.id,
            organization=user.department,
            status="draft",
            input_schema=parent_schema,
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


async def _cleanup_orphan_components(
    db: AsyncSession,
    user: User,
    plugin_cap: Capability,
    orphan_ids: set[Any],
) -> list[dict[str, Any]]:
    """重上传后清理不再引用的子能力：草稿删除，已发布则弃用。"""
    from app.models import Notification

    detached: list[dict[str, Any]] = []
    ids = [str(i) for i in orphan_ids if i]
    if not ids:
        return detached
    rows = (await db.scalars(select(Capability).where(Capability.id.in_(ids)))).all()
    for row in rows:
        if row.author_id != user.id:
            continue
        if "plugin-component" not in (row.tags or []):
            continue
        parent = (row.input_schema or {}).get("parent_plugin_id")
        if parent and str(parent) != str(plugin_cap.id):
            continue
        if row.status in ("draft", "returned", "rejected", "reviewing"):
            from app.services.capabilities import delete_capability_row

            await delete_capability_row(db, row, commit=False)
        elif row.status in ("published", "deprecated"):
            row.status = "deprecated"
            schema = dict(row.input_schema or {})
            schema.pop("parent_plugin_id", None)
            schema["detached_from_plugin"] = plugin_cap.id
            row.input_schema = schema
            detached.append(
                {
                    "type": row.type,
                    "name": row.name,
                    "version": row.version,
                    "capability_id": row.id,
                }
            )
            db.add(
                Notification(
                    user_id=row.author_id,
                    title=f"能力包组件 {row.name} v{row.version} 已从能力包断开并弃用",
                    body=f"父能力包 {plugin_cap.name} 重新上传后不再包含该组件。",
                    link=f"/capabilities/{row.id}",
                )
            )
    return detached


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
    old_ids = {
        str(c.get("capability_id"))
        for c in old
        if isinstance(c, dict) and c.get("capability_id")
    }

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
        old_ids.discard(str(cap.id))

    detached = await _cleanup_orphan_components(db, user, plugin_cap, old_ids)

    plugin_cap.input_schema = {
        "kind": "plugin",
        "plugin": {
            "name": plugin.get("name"),
            "description": plugin.get("description", ""),
            "version": plugin.get("version", plugin_cap.version),
            "author": plugin.get("author"),
            "homepage": plugin.get("homepage"),
            "repository": plugin.get("repository"),
            "license": plugin.get("license"),
            "keywords": plugin.get("keywords") or [],
        },
        "components": refs,
        "detached": detached,
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


async def cascade_plugin_components(
    db: AsyncSession,
    plugin_cap: Capability,
    *,
    action: str,
) -> None:
    """对 plugin 当前 components[] 内的子能力做状态级联。

    action: reject | return | withdraw | deprecate | delete
    """
    ids = plugin_component_ids(plugin_cap)
    if not ids:
        return
    rows = (await db.scalars(select(Capability).where(Capability.id.in_(ids)))).all()
    for row in rows:
        if "plugin-component" not in (row.tags or []):
            continue
        parent = (row.input_schema or {}).get("parent_plugin_id")
        if parent and str(parent) != str(plugin_cap.id):
            continue
        if action == "delete":
            if row.status in ("draft", "returned", "rejected", "reviewing"):
                from app.services.capabilities import delete_capability_row

                await delete_capability_row(db, row, commit=False)
            elif row.status in ("published", "deprecated"):
                row.status = "deprecated"
        elif action == "deprecate":
            if row.status == "published":
                row.status = "deprecated"
        elif action == "reject":
            if row.status in ("draft", "reviewing", "returned"):
                row.status = "rejected"
        elif action == "return":
            if row.status in ("draft", "reviewing", "rejected"):
                row.status = "returned"
        elif action == "withdraw":
            if row.status in ("reviewing", "returned", "rejected"):
                row.status = "draft"
