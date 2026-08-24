"""从 Agent 能力包提取内嵌 skills / MCP 元数据（Market Skills & MCP 规范对齐）。

支持来源：
- Skills A: agent.json 顶层 skills[]
- Skills B: skills/*.yaml（文件名作 name）
- Skills C: skills/*/skill.json（identity.name）
- MCP A1: agent.json.mcp_servers[]
- MCP A2: agent.json.mcp.required_servers[]
- MCP B: mcp/config.json 或 mcp/servers.json 的 mcpServers
"""

from __future__ import annotations

import json
from typing import Any


def _loads(raw: bytes | None) -> Any:
    if not raw:
        return None
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except Exception:
        return None


def _env_keys(env: Any) -> list[str]:
    if isinstance(env, dict):
        return [str(k) for k in env.keys()]
    if isinstance(env, list):
        return [str(x) for x in env if x]
    return []


def _skill_entry(
    *,
    name: str,
    display_name: str = "",
    description: str = "",
    version: str = "",
    category: str = "",
    icon: str = "",
) -> dict[str, Any]:
    return {
        "name": name,
        "display_name": display_name or name,
        "description": description or "",
        "version": version or "",
        "category": category or "",
        "icon": icon or "",
    }


def _mcp_entry(
    *,
    name: str,
    description: str = "",
    command: str = "",
    args: list | None = None,
    package: str = "",
    tools: list | None = None,
    required_env: list | None = None,
) -> dict[str, Any]:
    return {
        "name": name,
        "description": description or "",
        "command": command or "",
        "args": list(args or []),
        "package": package or "",
        "tools": list(tools or []),
        "required_env": list(required_env or []),
    }


def extract_embedded_skills(files: dict[str, bytes], agent_meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """归一化提取 Agent 包内 skills，同名去重（先声明优先）。"""
    meta = agent_meta if isinstance(agent_meta, dict) else _loads(files.get("agent.json")) or {}
    skills: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in meta.get("skills") or []:
        if not isinstance(item, dict):
            continue
        raw_name = str(item.get("name") or "").strip()
        if not raw_name or raw_name in seen:
            continue
        seen.add(raw_name)
        skills.append(
            _skill_entry(
                name=raw_name,
                display_name=str(item.get("display_name") or ""),
                description=str(item.get("description") or ""),
                version=str(item.get("version") or ""),
                category=str(item.get("category") or ""),
                icon=str(item.get("icon") or ""),
            )
        )

    for path in sorted(files):
        if not path.startswith("skills/") or not path.endswith(".yaml"):
            continue
        rest = path[len("skills/") :]
        if "/" in rest:
            continue
        raw_name = rest[: -len(".yaml")]
        if not raw_name or raw_name in seen:
            continue
        seen.add(raw_name)
        skills.append(
            _skill_entry(
                name=raw_name,
                description=f"Skill from {rest}",
                version=str((meta.get("identity") or {}).get("version") or meta.get("version") or ""),
            )
        )

    for path in sorted(files):
        if not path.startswith("skills/") or not path.endswith("/skill.json"):
            continue
        loaded = _loads(files.get(path))
        if not isinstance(loaded, dict):
            continue
        identity = loaded.get("identity") if isinstance(loaded.get("identity"), dict) else {}
        raw_name = str(identity.get("name") or loaded.get("name") or "").strip()
        if not raw_name:
            folder = path[len("skills/") :].rsplit("/", 1)[0]
            raw_name = folder.split("/")[0] if folder else ""
        if not raw_name or raw_name in seen:
            continue
        seen.add(raw_name)
        skills.append(
            _skill_entry(
                name=raw_name,
                display_name=str(identity.get("display_name") or loaded.get("display_name") or ""),
                description=str(identity.get("description") or loaded.get("description") or ""),
                version=str(identity.get("version") or loaded.get("version") or ""),
                category=str(identity.get("category") or loaded.get("category") or ""),
                icon=str(identity.get("icon") or loaded.get("icon") or ""),
            )
        )

    # skills/<name>/SKILL.md without skill.json
    for path in sorted(files):
        if not path.startswith("skills/") or not path.endswith("/SKILL.md"):
            continue
        folder = path[len("skills/") : -len("/SKILL.md")]
        if not folder or "/" in folder:
            continue
        if folder in seen:
            continue
        seen.add(folder)
        skills.append(_skill_entry(name=folder, description=f"Skill from skills/{folder}/"))

    return skills


def extract_embedded_mcp(files: dict[str, bytes], agent_meta: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """归一化提取 Agent 包内 MCP 依赖，同名去重（先声明优先）。"""
    meta = agent_meta if isinstance(agent_meta, dict) else _loads(files.get("agent.json")) or {}
    mcp_list: list[dict[str, Any]] = []
    seen: set[str] = set()

    for srv in meta.get("mcp_servers") or []:
        if not isinstance(srv, dict):
            continue
        raw_name = str(srv.get("name") or "").strip()
        if not raw_name or raw_name in seen:
            continue
        seen.add(raw_name)
        mcp_list.append(
            _mcp_entry(
                name=raw_name,
                description=str(srv.get("description") or ""),
                command=str(srv.get("command") or ""),
                args=srv.get("args") if isinstance(srv.get("args"), list) else [],
                package=str(srv.get("package") or ""),
                tools=srv.get("tools") if isinstance(srv.get("tools"), list) else [],
                required_env=_env_keys(srv.get("env")) or (
                    srv.get("required_env") if isinstance(srv.get("required_env"), list) else []
                ),
            )
        )

    mcp_cfg = meta.get("mcp") if isinstance(meta.get("mcp"), dict) else {}
    for srv in mcp_cfg.get("required_servers") or []:
        if not isinstance(srv, dict):
            continue
        raw_name = str(srv.get("name") or "").strip()
        if not raw_name or raw_name in seen:
            continue
        seen.add(raw_name)
        mcp_list.append(
            _mcp_entry(
                name=raw_name,
                description=str(srv.get("description") or ""),
                package=str(srv.get("package") or ""),
                tools=srv.get("tools") if isinstance(srv.get("tools"), list) else [],
                required_env=srv.get("required_env") if isinstance(srv.get("required_env"), list) else [],
            )
        )

    for cfg_path in ("mcp/config.json", "mcp/servers.json"):
        loaded = _loads(files.get(cfg_path))
        if not isinstance(loaded, dict):
            continue
        servers = loaded.get("mcpServers")
        if not isinstance(servers, dict):
            continue
        for raw_name, srv_cfg in servers.items():
            name = str(raw_name).strip()
            if not name or name in seen:
                continue
            if not isinstance(srv_cfg, dict):
                continue
            seen.add(name)
            mcp_list.append(
                _mcp_entry(
                    name=name,
                    description=str(srv_cfg.get("description") or ""),
                    command=str(srv_cfg.get("command") or ""),
                    args=srv_cfg.get("args") if isinstance(srv_cfg.get("args"), list) else [],
                    required_env=_env_keys(srv_cfg.get("env")),
                )
            )

    return mcp_list


def extract_agent_embedded(files: dict[str, bytes]) -> dict[str, Any]:
    """返回写入 Capability.input_schema 的 embedded_skills / embedded_mcp。"""
    meta = _loads(files.get("agent.json"))
    if not isinstance(meta, dict):
        meta = {}
    return {
        "embedded_skills": extract_embedded_skills(files, meta),
        "embedded_mcp": extract_embedded_mcp(files, meta),
    }


def _mark_version_mismatch(item: dict[str, Any], market_version: str) -> None:
    embedded_ver = str(item.get("version") or "").strip()
    if embedded_ver and market_version and embedded_ver != market_version:
        item["version_mismatch"] = True
    else:
        item.pop("version_mismatch", None)


async def enrich_embedded_with_market(
    db,
    schema: dict[str, Any] | None,
) -> dict[str, Any]:
    """为内嵌 skill/mcp 匹配市场上已发布的同名能力，写入 capability_id / market_version。"""
    from sqlalchemy import and_, select

    from app.models import Capability
    from app.services.capabilities import parse_semver

    schema = dict(schema or {})
    skills = list(schema.get("embedded_skills") or [])
    mcps = list(schema.get("embedded_mcp") or [])
    names = {str(s.get("name")) for s in skills if isinstance(s, dict) and s.get("name")}
    names |= {str(m.get("name")) for m in mcps if isinstance(m, dict) and m.get("name")}
    if not names:
        return schema

    rows = (
        await db.scalars(
            select(Capability).where(
                and_(
                    Capability.name.in_(names),
                    Capability.type.in_(("skill", "mcp")),
                    Capability.status.in_(("published", "deprecated")),
                )
            )
        )
    ).all()
    latest: dict[tuple[str, str], Capability] = {}
    for row in rows:
        key = (row.name, row.type)
        cur = latest.get(key)
        if cur is None or parse_semver(row.version) > parse_semver(cur.version):
            latest[key] = row

    out_skills = []
    for s in skills:
        if not isinstance(s, dict):
            continue
        item = dict(s)
        hit = latest.get((str(item.get("name")), "skill"))
        if hit is not None:
            item["capability_id"] = hit.id
            item["market_version"] = hit.version
            _mark_version_mismatch(item, hit.version)
        else:
            item.pop("capability_id", None)
            item.pop("market_version", None)
            item.pop("version_mismatch", None)
        out_skills.append(item)

    out_mcps = []
    for m in mcps:
        if not isinstance(m, dict):
            continue
        item = dict(m)
        hit = latest.get((str(item.get("name")), "mcp"))
        if hit is not None:
            item["capability_id"] = hit.id
            item["market_version"] = hit.version
            _mark_version_mismatch(item, hit.version)
        else:
            item.pop("capability_id", None)
            item.pop("market_version", None)
            item.pop("version_mismatch", None)
        out_mcps.append(item)

    schema["embedded_skills"] = out_skills
    schema["embedded_mcp"] = out_mcps
    return schema


async def find_used_by(db, cap) -> list[dict[str, Any]]:
    """查找引用该 skill/mcp 的已发布 Agent（内嵌）与 Plugin（components）。"""
    from sqlalchemy import select

    from app.models import Capability

    if cap.type not in ("skill", "mcp"):
        return []

    target_name = (cap.name or "").strip().lower()
    if not target_name:
        return []

    rows = (
        await db.scalars(
            select(Capability).where(
                Capability.type.in_(("agent", "plugin")),
                Capability.status.in_(("published", "deprecated")),
            )
        )
    ).all()

    used: list[dict[str, Any]] = []
    seen: set[str] = set()
    embed_key = "embedded_skills" if cap.type == "skill" else "embedded_mcp"

    for row in rows:
        schema = row.input_schema or {}
        matched = False
        if row.type == "agent":
            for item in schema.get(embed_key) or []:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("name") or "").strip().lower()
                if name != target_name:
                    continue
                # Prefer capability_id match when present
                cid = item.get("capability_id")
                if cid and cid != cap.id and name == target_name:
                    # same name but linked to another id — still count as usage by name
                    matched = True
                    break
                matched = True
                break
        elif row.type == "plugin":
            for item in schema.get("components") or []:
                if not isinstance(item, dict):
                    continue
                if item.get("type") != cap.type:
                    continue
                if str(item.get("name") or "").strip().lower() != target_name:
                    continue
                matched = True
                break
        if matched and row.id not in seen:
            seen.add(row.id)
            used.append(
                {
                    "type": row.type,
                    "name": row.name,
                    "version": row.version,
                    "capability_id": row.id,
                }
            )
    used.sort(key=lambda x: (x["type"], x["name"], x["version"]))
    return used