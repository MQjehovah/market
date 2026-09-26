"""能力包写入：把一份新的能力包内容应用到能力行（校验/拆解/物化/落盘）。

从 ``publish.upload_artifact`` 抽出的可复用逻辑，供「上传 zip」与「在线编辑文件树」共用。
"""

import io
import json
import zipfile
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models import Capability, CapabilityArtifact, User
from app.services.packages import extract_readme_text, prepare_package
from app.storage import get_storage


async def apply_package(
    db: AsyncSession,
    user: User,
    cap: Capability,
    content: bytes,
    *,
    filename: str | None = None,
) -> Capability:
    """校验并应用能力包内容到 ``cap``（不改变状态），返回带关联的刷新后能力行。"""
    content, details = prepare_package(cap.type, content)

    cap.readme_md = extract_readme_text(content)
    files_map = details.get("files") or {}
    cap.validation_report = {
        "ok": not bool(details.get("errors")),
        "warnings": list(details.get("warnings") or []),
        "errors": list(details.get("errors") or []),
        "files": sorted(files_map.keys()) if isinstance(files_map, dict) else list(files_map),
        "meta": {
            k: details.get(k)
            for k in ("meta", "connection", "schema")
            if k in details and not isinstance(details.get(k), (bytes, bytearray))
        },
    }
    # strip heavy blobs from meta
    meta = cap.validation_report.get("meta") or {}
    for k, v in list(meta.items()):
        if isinstance(v, dict):
            meta[k] = {kk: vv for kk, vv in v.items() if not isinstance(vv, (bytes, bytearray))}

    if cap.type == "tool" and details.get("schema"):
        cap.input_schema = details["schema"]
    if cap.type == "agent":
        from app.services.agent_metadata import enrich_embedded_with_market, extract_agent_embedded

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            logical = {logical: zf.read(raw) for logical, raw in details["files"].items()}
        embedded = extract_agent_embedded(logical)
        schema = dict(cap.input_schema or {})
        schema["embedded_skills"] = embedded["embedded_skills"]
        schema["embedded_mcp"] = embedded["embedded_mcp"]
        cap.input_schema = await enrich_embedded_with_market(db, schema)
    if cap.type in ("skill", "rule", "command") and details.get("meta"):
        meta = details["meta"]
        identity = meta.get("identity") if isinstance(meta.get("identity"), dict) else {}
        schema = {
            "kind": cap.type,
            "name": meta.get("name") or identity.get("name") or cap.name,
            "version": meta.get("version") or identity.get("version") or cap.version,
            "description": meta.get("description") or identity.get("description") or "",
            "category": meta.get("category") or identity.get("category") or cap.category or "",
            "display_name": identity.get("display_name") or meta.get("display_name") or "",
        }
        if cap.type == "rule":
            schema["alwaysApply"] = bool(meta.get("alwaysApply") or meta.get("always_apply"))
            globs = meta.get("globs") or ""
            schema["globs"] = globs if isinstance(globs, str) else ",".join(globs or [])
        cap.input_schema = schema
    if cap.type == "mcp":
        meta = details.get("meta") or {}
        conn = details.get("connection") or {}
        env = conn.get("env") if isinstance(conn.get("env"), dict) else {}
        tools_summary: list[dict[str, str]] = []
        try:
            with zipfile.ZipFile(io.BytesIO(content)) as zf:
                if "tools.json" in zf.namelist():
                    raw_tools = json.loads(zf.read("tools.json").decode("utf-8-sig"))
                    rows = (
                        raw_tools.get("tools")
                        if isinstance(raw_tools, dict)
                        else raw_tools
                        if isinstance(raw_tools, list)
                        else []
                    )
                    for item in rows or []:
                        if not isinstance(item, dict) or not item.get("name"):
                            continue
                        tools_summary.append(
                            {
                                "name": str(item.get("name")),
                                "description": str(item.get("description") or ""),
                            }
                        )
        except Exception:  # noqa: BLE001
            tools_summary = []
        from app.services.mcp_editor import public_env_map

        headers = conn.get("headers") if isinstance(conn.get("headers"), dict) else {}
        cap.input_schema = {
            "kind": "mcp",
            "name": meta.get("name") or cap.name,
            "version": meta.get("version") or cap.version,
            "description": meta.get("description") or "",
            "transport": conn.get("transport") or conn.get("type") or "stdio",
            "command": conn.get("command") or "",
            "args": list(conn.get("args") or []) if isinstance(conn.get("args"), list) else [],
            "url": conn.get("url") or "",
            "server": conn.get("server") or "",
            "header_keys": [str(k) for k in headers.keys()],
            "required_env": [str(k) for k in env.keys()],
            "env": public_env_map(env),
            "tools": tools_summary,
        }
    if cap.type == "workflow" and details.get("meta"):
        meta = details["meta"]
        nodes = meta.get("nodes") if isinstance(meta.get("nodes"), list) else []
        edges = meta.get("edges") if isinstance(meta.get("edges"), list) else []
        cap.input_schema = {
            "kind": "workflow",
            "name": meta.get("name") or cap.name,
            "version": meta.get("version") or cap.version,
            "node_count": len(nodes),
            "edge_count": len(edges),
            "node_types": sorted(
                {str(n.get("type")) for n in nodes if isinstance(n, dict) and n.get("type")}
            ),
        }
    if cap.type == "hook":
        meta = details.get("meta") or {}
        hooks_cfg = details.get("hooks") or {}
        events = hooks_cfg.get("hooks") if isinstance(hooks_cfg.get("hooks"), dict) else {}
        cap.input_schema = {
            "kind": "hook",
            "name": meta.get("name") or cap.name,
            "version": meta.get("version") or cap.version,
            "description": meta.get("description") or "",
            "events": sorted(str(k) for k in events.keys()) if isinstance(events, dict) else [],
        }
    if cap.type == "plugin":
        from app.services.plugins import materialize_plugin_components

        await materialize_plugin_components(db, user, cap, content, details)

    artifact_name = filename or f"{cap.name}-{cap.version}.zip"
    info = get_storage().save(cap.id, artifact_name, io.BytesIO(content))
    db.add(CapabilityArtifact(capability_id=cap.id, filename=artifact_name, **info))
    await db.commit()
    return await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts), joinedload(Capability.author))
        .where(Capability.id == cap.id)
    )
