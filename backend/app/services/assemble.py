"""Agent 组装：人设（agent 能力）+ 工具/技能/MCP 依赖 → 生成可发布的 agent 包。

产物 zip 结构：
    agent.json          组装元信息（base persona + dependencies 清单）
    PROMPT.md           人设提示词（原样保留）
    dependencies.json   完整依赖清单 [{name, type, version}]
    tools.json          工具清单 [{name, version, schema}]
    tools/<name>/tool.py       工具源码快照（供本地安装）
    skills/<name>/SKILL.md     技能快照（含 references/scripts/assets）
    mcp/<name>/connection.json MCP 连接配置快照
"""

import io
import json
import zipfile
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.models import Capability, CapabilityArtifact, User
from app.schemas import AssembleDependency, AssembleRequest
from app.services.capabilities import (
    ensure_name_ownership,
    normalize_cap_name,
    parse_semver,
    to_capability_out,
)
from app.storage import get_storage


def _unzip(content: bytes) -> dict[str, bytes]:
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return {name: zf.read(name) for name in zf.namelist() if not name.endswith("/")}
    except zipfile.BadZipFile as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "能力包不是有效的 zip") from exc


async def _resolve_published_cap(
    db: AsyncSession, user: User | None, name: str, type_: str, version: str = ""
) -> Capability:
    """按名称解析已发布的可见能力，要求已上传能力包。"""
    from app.services.capabilities import get_visible_capabilities

    visible = await get_visible_capabilities(db, user)
    matches = [
        c
        for c in visible
        if c.name == name and c.type == type_ and c.status in ("published", "deprecated")
    ]
    if not matches:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{type_}:{name} 不存在或未发布")
    cap = (
        next((c for c in matches if c.version == version), None)
        if version
        else max(matches, key=lambda c: parse_semver(c.version))
    )
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{type_}:{name} 不存在版本 {version}")
    if not cap.artifacts:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{type_}:{name} 未上传能力包")
    return cap


def _artifact_bytes(cap: Capability) -> bytes:
    return get_storage().open(cap.artifacts[-1].uri).read()


def _collect_package_dir(files: dict[str, bytes], prefix: str) -> dict[str, bytes]:
    """收集能力包中指定前缀（如 skills/、references/）下的文件。"""
    out = {}
    for name, data in files.items():
        if name == prefix.rstrip("/"):
            continue
        if name.startswith(prefix):
            out[name] = data
    return out


def build_assembled_package(
    persona: Capability,
    deps: list[tuple[Capability, AssembleDependency]],
    data: AssembleRequest,
) -> bytes:
    """组装 agent 包字节（纯函数，便于测试与播种）。"""
    persona_files = _unzip(_artifact_bytes(persona))
    if "PROMPT.md" not in persona_files:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, f"人设 {persona.name} 缺少 PROMPT.md"
        )

    manifest: list[dict[str, str]] = []
    tools: list[dict[str, Any]] = []
    skills: list[dict[str, str]] = []
    mcps: list[dict[str, str]] = []
    files: dict[str, bytes] = {}

    for cap, dep in deps:
        manifest.append({"name": cap.name, "type": dep.type, "version": cap.version})
        dep_files = _unzip(_artifact_bytes(cap))
        if dep.type == "tool":
            tools.append({"name": cap.name, "version": cap.version, "schema": cap.input_schema or {}})
            impl = dep_files.get("implementation/tool.py")
            if impl is not None:
                files[f"tools/{cap.name}/tool.py"] = impl
        elif dep.type == "skill":
            skills.append({"name": cap.name, "version": cap.version})
            skill_md = dep_files.get("SKILL.md")
            if skill_md is not None:
                files[f"skills/{cap.name}/SKILL.md"] = skill_md
                for name, content in _collect_package_dir(dep_files, "references/").items():
                    files[f"skills/{cap.name}/{name}"] = content
                for name, content in _collect_package_dir(dep_files, "scripts/").items():
                    files[f"skills/{cap.name}/{name}"] = content
                for name, content in _collect_package_dir(dep_files, "assets/").items():
                    files[f"skills/{cap.name}/{name}"] = content
        elif dep.type == "mcp":
            mcps.append({"name": cap.name, "version": cap.version})
            conn = dep_files.get("connection.json")
            if conn is not None:
                files[f"mcp/{cap.name}/connection.json"] = conn
            meta = dep_files.get("mcp.json")
            if meta is not None:
                files[f"mcp/{cap.name}/mcp.json"] = meta

    out = {
        "agent.json": json.dumps(
            {
                "name": data.name,
                "description": data.description,
                "version": data.version,
                "role": data.name,
                "assembled": True,
                "base_persona": {"name": persona.name, "version": persona.version},
                "dependencies": manifest,
            },
            ensure_ascii=False,
            indent=2,
        ).encode("utf-8"),
        "PROMPT.md": persona_files["PROMPT.md"],
        "dependencies.json": json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"),
        "tools.json": json.dumps(tools, ensure_ascii=False, indent=2).encode("utf-8"),
    }
    if "TEAM.md" in persona_files:
        out["TEAM.md"] = persona_files["TEAM.md"]
    out.update(files)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in out.items():
            zf.writestr(name, content)
    return buf.getvalue()


async def assemble_agent(db: AsyncSession, user: User, data: AssembleRequest) -> Capability:
    """组装并创建 agent 能力（draft + 能力包工件）。"""
    # 名称规范化：落库与包内 agent.json 保持一致（防首尾空白绕过按名判定）
    data.name = normalize_cap_name(data.name)
    persona = await _resolve_published_cap(db, user, data.persona, "agent", data.persona_version)
    dep_caps: list[tuple[Capability, AssembleDependency]] = []
    seen: set[tuple[str, str]] = set()
    for dep in data.dependencies:
        key = (dep.type, dep.name)
        if key in seen:
            continue
        seen.add(key)
        dep_caps.append((await _resolve_published_cap(db, user, dep.name, dep.type, dep.version), dep))

    exists = await db.scalar(
        select(Capability.id).where(
            and_(Capability.name == data.name, Capability.version == data.version)
        )
    )
    if exists:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"能力 {data.name} 已存在版本 {data.version}"
        )
    # 名称归属：同名只能由既有作者继续发版本（防抢注同名后污染按名资源）
    await ensure_name_ownership(db, data.name, user)

    pkg = build_assembled_package(persona, dep_caps, data)
    cap = Capability(
        name=data.name,
        description=data.description
        or f"组装 Agent：{persona.name} + {len(data.dependencies)} 个能力依赖",
        type="agent",
        version=data.version,
        category=data.category or "组装",
        tags=data.tags or ["组装"],
        visibility="internal",
        status="draft",
        author_id=user.id,
        organization=user.organization,
        input_schema={},
    )
    db.add(cap)
    await db.flush()
    info = get_storage().save(cap.id, f"{cap.name}-{cap.version}.zip", io.BytesIO(pkg))
    db.add(CapabilityArtifact(capability_id=cap.id, filename=f"{cap.name}-{cap.version}.zip", **info))
    await db.commit()
    await db.refresh(cap)
    return cap
