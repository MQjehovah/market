"""Agent 专家插件包读写工具（与桌面本地内核的 claude 插件式布局一致）。

包结构：
    plugin.json          根描述（name/description/version/role/dependencies）
    agents/<name>.md     主提示词（frontmatter name/description + 正文 prompt）
    skills/<n>/SKILL.md  技能（含 references/scripts/assets）
    mcps/<n>/connection.json  MCP 连接配置（每个一个目录）
    TEAM.md              团队编排（可选）

替换旧格式 agent.json / PROMPT.md / dependencies.json / tools.json / mcp/<n>/connection.json。
"""

from __future__ import annotations

import json
from typing import Any

PLUGIN_JSON = "plugin.json"
# 旧格式（读侧兼容，不回写）：agent.json / PROMPT.md / dependencies.json
LEGACY_AGENT_JSON = "agent.json"
LEGACY_PROMPT = "PROMPT.md"
LEGACY_DEPS = "dependencies.json"


def agent_md_path(name: str) -> str:
    return f"agents/{name}.md"


def build_agent_md(name: str, description: str, prompt: str) -> bytes:
    fm = f"---\nname: {name}\ndescription: {(description or '').strip()}\n---\n"
    return (fm + (prompt or "")).encode("utf-8")


def parse_frontmatter(raw: str) -> tuple[dict[str, str], str]:
    lines = raw.replace("\ufeff", "").split("\n")
    meta: dict[str, str] = {}
    body_start = 0
    if lines and lines[0].strip() == "---":
        for i in range(1, len(lines)):
            if lines[i].strip() == "---":
                body_start = i + 1
                break
            if ":" in lines[i]:
                key, _, value = lines[i].partition(":")
                meta[key.strip()] = value.strip().strip('"').strip("'")
    return meta, "\n".join(lines[body_start:]).strip()


def parse_agent_md(raw: bytes, fallback_name: str) -> tuple[str, str, str]:
    meta, body = parse_frontmatter(raw.decode("utf-8", errors="replace"))
    return meta.get("name") or fallback_name, meta.get("description", ""), body


def build_plugin_json(
    *,
    name: str,
    description: str,
    version: str,
    role: str = "",
    dependencies: list[dict[str, str]] | None = None,
    assembled: bool = False,
    editable: bool = False,
    base_persona: dict[str, str] | None = None,
) -> bytes:
    data: dict[str, Any] = {"name": name, "description": description or "", "version": version}
    if role:
        data["role"] = role
    if assembled:
        data["assembled"] = True
    if editable:
        data["editable"] = True
    if base_persona:
        data["base_persona"] = base_persona
    data["dependencies"] = list(dependencies or [])
    return json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")


def parse_plugin_meta(files: dict[str, bytes]) -> dict[str, Any]:
    raw = files.get(PLUGIN_JSON)
    if raw is None:
        raw = files.get(LEGACY_AGENT_JSON)
    if raw is None:
        return {}
    try:
        val = json.loads(raw.decode("utf-8-sig"))
    except Exception:  # noqa: BLE001
        return {}
    return val if isinstance(val, dict) else {}


def _legacy_deps(files: dict[str, bytes]) -> list[dict[str, Any]]:
    raw = files.get(LEGACY_DEPS)
    if raw is None:
        return []
    try:
        val = json.loads(raw.decode("utf-8-sig"))
    except Exception:  # noqa: BLE001
        return []
    if isinstance(val, list):
        return [d for d in val if isinstance(d, dict)]
    if isinstance(val, dict) and isinstance(val.get("dependencies"), list):
        return [d for d in val["dependencies"] if isinstance(d, dict)]
    return []


def load_prompt_deps(files: dict[str, bytes], name: str = "") -> tuple[str, list[dict[str, str]]]:
    """从插件包读取主提示词与依赖清单（供编辑/运行）；兼容旧格式 agent.json/PROMPT.md/dependencies.json。"""
    meta = parse_plugin_meta(files)
    prompt = ""
    candidate = agent_md_path(name) if name else ""
    if candidate and candidate in files:
        _, _, prompt = parse_agent_md(files[candidate], name)
    else:
        for path in sorted(files):
            if path.startswith("agents/") and path.endswith(".md"):
                fallback = path[len("agents/") : -len(".md")]
                _, _, prompt = parse_agent_md(files[path], fallback)
                break
    if not prompt and LEGACY_PROMPT in files:
        prompt = files[LEGACY_PROMPT].decode("utf-8", errors="replace").strip()
    deps = meta.get("dependencies")
    if not isinstance(deps, list):
        deps = _legacy_deps(files)
    return prompt, [d for d in deps if isinstance(d, dict)]


def _mcp_servers_from_files(files: dict[str, bytes]) -> list[dict[str, Any]]:
    """旧格式内嵌 MCP：优先 agent.json.mcp_servers，其次顶层 mcp_servers.json。"""
    meta = parse_plugin_meta(files)
    servers = meta.get("mcp_servers")
    if isinstance(servers, list):
        return [s for s in servers if isinstance(s, dict)]
    raw = files.get("mcp_servers.json")
    if raw is not None:
        try:
            val = json.loads(raw.decode("utf-8-sig"))
        except Exception:  # noqa: BLE001
            return []
        if isinstance(val, list):
            return [s for s in val if isinstance(s, dict)]
        if isinstance(val, dict) and isinstance(val.get("mcpServers"), dict):
            out = []
            for sn, cfg in val["mcpServers"].items():
                if isinstance(cfg, dict):
                    out.append({"name": sn, **cfg})
            return out
    return []


def legacy_agent_to_new(
    files: dict[str, bytes], fallback_name: str = ""
) -> dict[str, bytes] | None:
    """旧格式专家包 → 新格式（plugin.json + agents/<name>.md + mcps/<n>/connection.json）。

    已是新格式返回 None。保留 skills/ 及其它附件；移除旧 meta 文件与旧 mcp/ 目录。
    """
    has_new = PLUGIN_JSON in files and any(
        p.startswith("agents/") and p.endswith(".md") for p in files
    )
    if has_new:
        return None
    meta = parse_plugin_meta(files)
    name = str(meta.get("name") or fallback_name or "").strip()
    if not name:
        return None
    description = str(meta.get("description") or "")
    version = str(meta.get("version") or "0.1.0")
    role = str(meta.get("role") or "")
    prompt, deps = load_prompt_deps(files, name)
    servers = _mcp_servers_from_files(files)

    drop = {LEGACY_AGENT_JSON, LEGACY_PROMPT, LEGACY_DEPS, "mcp_servers.json"}
    out: dict[str, bytes] = {}
    for path, data in files.items():
        if path in drop:
            continue
        if path.startswith("mcp/") or path.startswith("mcps/"):
            continue  # 由 mcps/<n>/connection.json 重建
        out[path] = data

    out[PLUGIN_JSON] = build_plugin_json(
        name=name, description=description, version=version, role=role, dependencies=deps
    )
    out[agent_md_path(name)] = build_agent_md(name, description, prompt)
    for s in servers:
        sn = str(s.get("name") or "").strip()
        if not sn:
            continue
        conn = {
            "name": sn,
            "transport": s.get("transport") or "stdio",
            "command": s.get("command") or "python",
            "args": list(s.get("args") or []),
            "env": s.get("env") or {},
            "description": s.get("description") or "",
            "enabled": s.get("enabled", True),
        }
        out[f"mcps/{sn}/connection.json"] = json.dumps(
            conn, ensure_ascii=False, indent=2
        ).encode("utf-8")
    return out


def connection_to_mcpserver(conn: dict[str, Any]) -> dict[str, Any] | None:
    """把 MCP 能力包的 connection.json 转为内核可读的连接配置；无法本地直连(gateway/缺字段)返回 None。"""
    if not isinstance(conn, dict):
        return None
    transport = conn.get("transport")
    command = conn.get("command")
    url = conn.get("url")
    if transport in ("http", "streamable_http", "streamable-http"):
        if not url:
            return None
        entry: dict[str, Any] = {"url": url, "transport": "http"}
        if isinstance(conn.get("headers"), dict):
            entry["headers"] = conn["headers"]
        return entry
    if transport == "sse":
        if not url:
            return None
        entry = {"url": url, "transport": "sse"}
        if isinstance(conn.get("headers"), dict):
            entry["headers"] = conn["headers"]
        return entry
    if transport == "stdio" or (command and not url):
        if not command:
            return None
        entry = {"command": command}
        if isinstance(conn.get("args"), list):
            entry["args"] = conn["args"]
        if isinstance(conn.get("env"), dict):
            entry["env"] = conn["env"]
        if isinstance(conn.get("cwd"), str) and conn["cwd"]:
            entry["cwd"] = conn["cwd"]
        return entry
    return None
