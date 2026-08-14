"""Agent 编辑：提示词 + 绑定能力一体化编辑，保存即生成新版本草稿（提示词与依赖进入能力包）。"""

import io
import json
import zipfile
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Capability, CapabilityArtifact, User
from app.schemas import AgentEditSave
from app.services.capabilities import (
    get_visible_capabilities,
    next_version,
    parse_semver,
)
from app.storage import get_storage


def _read_zip(cap: Capability) -> dict[str, bytes] | None:
    if not cap.artifacts:
        return None
    try:
        content = get_storage().open(cap.artifacts[-1].uri).read()
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return {
                name: zf.read(name)
                for name in zf.namelist()
                if not name.endswith("/")
            }
    except Exception:  # noqa: BLE001
        return None


def read_prompt_deps(cap: Capability) -> tuple[str, list[dict[str, str]]]:
    files = _read_zip(cap) or {}
    prompt = files.get("PROMPT.md", b"").decode("utf-8", errors="replace")
    try:
        deps = json.loads(files.get("dependencies.json", b"[]").decode("utf-8"))
        deps = deps if isinstance(deps, list) else []
    except (ValueError, UnicodeDecodeError):
        deps = []
    return prompt, deps


async def _agent_versions(db: AsyncSession, name: str) -> list[Capability]:
    rows = (
        await db.scalars(
            select(Capability)
            .options(selectinload(Capability.artifacts))
            .where(and_(Capability.name == name, Capability.type == "agent"))
        )
    ).all()
    return list(rows)


def _deps_payload(deps: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [
        {
            "name": str(d.get("name", "")),
            "type": str(d.get("type", "")),
            "version": str(d.get("version", "") or ""),
        }
        for d in deps
        if d.get("name") and d.get("type")
    ]


def build_agent_package(
    *,
    base: Capability | None,
    name: str,
    description: str,
    version: str,
    prompt: str,
    deps: list[dict[str, str]],
) -> bytes:
    """重建 agent 能力包：保留人设附属文件（TEAM/skills/agents…），替换 PROMPT.md 与依赖清单。"""
    files: dict[str, bytes] = {}
    if base is not None:
        for fname, content in (_read_zip(base) or {}).items():
            if fname in ("agent.json", "PROMPT.md", "dependencies.json", "tools.json"):
                continue
            files[fname] = content
    files["PROMPT.md"] = prompt.encode("utf-8")
    files["dependencies.json"] = json.dumps(deps, ensure_ascii=False, indent=2).encode("utf-8")
    files["tools.json"] = json.dumps(
        [{"name": d["name"], "version": d["version"]} for d in deps if d["type"] == "tool"],
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    files["agent.json"] = json.dumps(
        {
            "name": name,
            "description": description,
            "version": version,
            "role": name,
            "editable": True,
            "dependencies": deps,
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
    return buf.getvalue()


async def get_editable(
    db: AsyncSession, user: User | None, name: str
) -> tuple[Capability, str, list[dict[str, str]], str]:
    versions = await _agent_versions(db, name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent {name} 不存在")
    draft = next(
        (
            c
            for c in versions
            if c.status in ("draft", "returned", "rejected")
            and (user is None or user.role == "admin" or c.author_id == user.id)
        ),
        None,
    )
    published = [c for c in versions if c.status in ("published", "deprecated")]
    cap = draft or (max(published, key=lambda c: parse_semver(c.version)) if published else None)
    if cap is None:
        cap = max(versions, key=lambda c: parse_semver(c.version))
    prompt, deps = read_prompt_deps(cap)
    base_version = max(published, key=lambda c: parse_semver(c.version)).version if published else ""
    return cap, prompt, deps, base_version


async def save_version(
    db: AsyncSession, user: User, name: str, data: AgentEditSave
) -> tuple[Capability, str, list[dict[str, str]]]:
    versions = await _agent_versions(db, name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Agent {name} 不存在")
    draft = next((c for c in versions if c.status in ("draft", "returned", "rejected")), None)
    published = [c for c in versions if c.status in ("published", "deprecated")]
    base = max(published, key=lambda c: parse_semver(c.version)) if published else None
    deps = _deps_payload([d.model_dump() for d in data.dependencies])

    if draft is not None:
        if user.role != "admin" and draft.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限编辑该草稿")
        cap = draft
        prompt = data.prompt or read_prompt_deps(cap)[0]
    else:
        if base is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "该 Agent 没有已发布版本，无法创建新版本",
            )
        base_prompt, _ = read_prompt_deps(base)
        new_version = data.new_version or next_version(base.version, "patch")
        exists = await db.scalar(
            select(Capability.id).where(
                and_(
                    Capability.name == name,
                    Capability.version == new_version,
                    Capability.type == "agent",
                )
            )
        )
        if exists:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"Agent {name} 已存在版本 {new_version}"
            )
        cap = Capability(
            name=name,
            description=data.description or base.description,
            type="agent",
            version=new_version,
            category=data.category or base.category,
            tags=data.tags or list(base.tags or []),
            visibility=base.visibility,
            status="draft",
            author_id=user.id,
            organization=user.organization,
        )
        db.add(cap)
        await db.flush()
        prompt = data.prompt or base_prompt

    if data.description:
        cap.description = data.description
    if data.category:
        cap.category = data.category
    if data.tags:
        cap.tags = data.tags

    pkg = build_agent_package(
        base=base,
        name=cap.name,
        description=cap.description,
        version=cap.version,
        prompt=prompt,
        deps=deps,
    )
    info = get_storage().save(cap.id, f"{cap.name}-{cap.version}.zip", io.BytesIO(pkg))
    db.add(
        CapabilityArtifact(
            capability_id=cap.id, filename=f"{cap.name}-{cap.version}.zip", **info
        )
    )
    await db.commit()
    await db.refresh(cap)
    return cap, prompt, deps
