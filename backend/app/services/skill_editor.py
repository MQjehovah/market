"""技能在线编辑：读取 / 保存 SKILL.md（保存即新版本草稿，附属文件保留）。"""

import io
import json
import zipfile
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Capability, CapabilityArtifact, User
from app.schemas import SkillEditSave
from app.services.capabilities import (
    draft_policy_kwargs,
    merge_edit_package_files,
    next_version,
    package_base_for_save,
    parse_semver,
    read_capability_files,
    text_file,
)
from app.storage import get_storage


def _meta(files: dict[str, bytes] | None) -> dict[str, Any]:
    try:
        return json.loads((files or {}).get("skill.json", b"{}").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


def _file_list(files: dict[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": name, "size": len(data)}
        for name, data in files.items()
        if name not in ("skill.json", "SKILL.md")
    ]


def _file_list_from_zip(pkg: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(pkg)) as zf:
        return [
            {"path": name, "size": zf.getinfo(name).file_size}
            for name in zf.namelist()
            if not name.endswith("/")
        ]


async def _skill_versions(db: AsyncSession, name: str) -> list[Capability]:
    rows = (
        await db.scalars(
            select(Capability)
            .options(selectinload(Capability.artifacts))
            .where(and_(Capability.name == name, Capability.type == "skill"))
        )
    ).all()
    return list(rows)


async def get_editable(
    db: AsyncSession, user: User | None, name: str
) -> tuple[Capability, str, list[dict[str, Any]], str]:
    versions = await _skill_versions(db, name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"技能 {name} 不存在")
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
        cap, published, core_text_files=frozenset({"SKILL.md"})
    )
    skill_md = text_file(files, "SKILL.md")
    base_version = (
        max(published, key=lambda c: parse_semver(c.version)).version if published else ""
    )
    return cap, skill_md, _file_list(files), base_version


def build_skill_package(
    *,
    base: Capability | None,
    name: str,
    description: str,
    version: str,
    skill_md: str,
    category: str,
    tags: list[str],
) -> bytes:
    """重建技能包：保留 references/scripts/assets 等附属文件，替换 SKILL.md 与 skill.json。"""
    files: dict[str, bytes] = {}
    base_files = read_capability_files(base) if base is not None else {}
    for fname, content in base_files.items():
        if fname in ("skill.json", "SKILL.md"):
            continue
        files[fname] = content
    files["SKILL.md"] = skill_md.encode("utf-8")
    meta = _meta(base_files)
    files["skill.json"] = json.dumps(
        {
            "name": name,
            "description": description or meta.get("description", ""),
            "version": version,
            "category": category or meta.get("category", ""),
            "tags": tags if tags is not None else (meta.get("tags") or []),
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
    return buf.getvalue()


async def save_version(
    db: AsyncSession, user: User, name: str, data: SkillEditSave
) -> tuple[Capability, str, list[dict[str, Any]]]:
    versions = await _skill_versions(db, name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"技能 {name} 不存在")
    draft = next((c for c in versions if c.status in ("draft", "returned", "rejected")), None)
    published = [c for c in versions if c.status in ("published", "deprecated")]
    base = max(published, key=lambda c: parse_semver(c.version)) if published else None
    inherit = merge_edit_package_files(
        draft or base or versions[0], published, core_text_files=frozenset({"SKILL.md"})
    )
    inherit_md = text_file(inherit, "SKILL.md")

    if draft is not None:
        if user.role != "admin" and draft.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限编辑该草稿")
        cap = draft
        skill_md = data.skill_md if str(data.skill_md or "").strip() else inherit_md
    else:
        if base is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "该技能没有已发布版本，无法创建新版本",
            )
        if user.role != "admin" and base.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "只能编辑自己发布的技能")
        new_version = data.new_version or next_version(base.version, "patch")
        exists = await db.scalar(
            select(Capability.id).where(
                and_(
                    Capability.name == name,
                    Capability.version == new_version,
                    Capability.type == "skill",
                )
            )
        )
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, f"技能 {name} 已存在版本 {new_version}")
        cap = Capability(
            name=name,
            description=data.description or base.description,
            type="skill",
            version=new_version,
            category=data.category or base.category,
            tags=data.tags if data.tags is not None else list(base.tags or []),
            visibility=base.visibility,
            status="draft",
            author_id=user.id,
            organization=user.organization,
            **draft_policy_kwargs(base),
        )
        db.add(cap)
        await db.flush()
        skill_md = data.skill_md if str(data.skill_md or "").strip() else inherit_md

    if data.description:
        cap.description = data.description
    if data.category:
        cap.category = data.category
    if data.tags is not None:
        cap.tags = list(data.tags)

    pkg = build_skill_package(
        base=package_base_for_save(draft, published) or base,
        name=cap.name,
        description=cap.description or "",
        version=cap.version,
        skill_md=skill_md,
        category=cap.category or "",
        tags=list(cap.tags or []),
    )
    info = get_storage().save(cap.id, f"{cap.name}-{cap.version}.zip", io.BytesIO(pkg))
    db.add(
        CapabilityArtifact(
            capability_id=cap.id, filename=f"{cap.name}-{cap.version}.zip", **info
        )
    )
    await db.commit()
    return cap, skill_md, _file_list_from_zip(pkg)
