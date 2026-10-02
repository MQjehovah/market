"""Agent 编辑：提示词 + 绑定能力一体化编辑，保存即生成新版本草稿（提示词与依赖进入能力包）。"""

import io
import zipfile
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Capability, CapabilityArtifact, User
from app.schemas import AgentEditSave
from app.services.agent_package import (
    agent_md_path,
    build_agent_md,
    build_plugin_json,
    load_prompt_deps,
    parse_plugin_meta,
)
from app.services.capabilities import (
    draft_policy_kwargs,
    merge_edit_package_files,
    next_version,
    package_base_for_save,
    parse_semver,
    read_capability_files,
)
from app.storage import get_storage


def _read_zip(cap: Capability) -> dict[str, bytes] | None:
    files = read_capability_files(cap)
    return files or None


def _prompt_deps_from_files(files: dict[str, bytes]) -> tuple[str, list[dict[str, str]]]:
    meta = parse_plugin_meta(files)
    name = str(meta.get("name") or "")
    prompt, deps = load_prompt_deps(files, name)
    return prompt, deps


def read_prompt_deps(cap: Capability) -> tuple[str, list[dict[str, str]]]:
    return _prompt_deps_from_files(read_capability_files(cap))


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
    """重建 agent 插件包：保留附属文件（TEAM/skills/mcps…），替换 plugin.json 与 agents/<name>.md。"""
    files: dict[str, bytes] = {}
    if base is not None:
        for fname, content in read_capability_files(base).items():
            if fname in ("plugin.json", "agent.json", "PROMPT.md", "dependencies.json", "tools.json"):
                continue
            if fname.startswith("tools/") or fname.startswith("agents/"):
                continue  # 主提示词文件由下方重建
            files[fname] = content
    files["plugin.json"] = build_plugin_json(
        name=name, description=description, version=version, role=name, editable=True, dependencies=deps
    )
    files[agent_md_path(name)] = build_agent_md(name, description, prompt)
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
    files = merge_edit_package_files(
        cap, published, core_text_files=frozenset({"plugin.json", "agents/*.md"})
    )
    prompt, deps = _prompt_deps_from_files(files)
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
    inherit_files = merge_edit_package_files(
        draft or base or versions[0],
        published,
        core_text_files=frozenset({"plugin.json", "agents/*.md"}),
    )
    inherit_prompt, _ = _prompt_deps_from_files(inherit_files)

    if draft is not None:
        if user.role != "admin" and draft.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限编辑该草稿")
        cap = draft
        prompt = data.prompt if str(data.prompt or "").strip() else inherit_prompt
    else:
        if base is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "该 Agent 没有已发布版本，无法创建新版本",
            )
        if user.role != "admin" and base.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "只能编辑自己发布的 Agent")
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
            organization=user.department,
            **draft_policy_kwargs(base),
        )
        db.add(cap)
        await db.flush()
        prompt = data.prompt if str(data.prompt or "").strip() else inherit_prompt

    if data.description:
        cap.description = data.description
    if data.category:
        cap.category = data.category
    if data.tags:
        cap.tags = data.tags

    pkg = build_agent_package(
        base=package_base_for_save(draft, published) or base,
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
