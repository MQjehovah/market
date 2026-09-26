"""rule / command 在线编辑：读取正文、保存新版本草稿，附属文件保留。"""

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
from app.schemas import MarkdownKindEditSave
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

KIND_FILES = {
    "rule": {"meta": "rule.json", "body": "RULE.mdc", "label": "规则"},
    "command": {"meta": "command.json", "body": "COMMAND.md", "label": "命令"},
}


def _spec(kind: str) -> dict[str, str]:
    spec = KIND_FILES.get(kind)
    if spec is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"不支持的编辑类型：{kind}")
    return spec


def _meta(files: dict[str, bytes] | None, meta_file: str) -> dict[str, Any]:
    try:
        return json.loads((files or {}).get(meta_file, b"{}").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


def _file_list(files: dict[str, bytes], skip: set[str]) -> list[dict[str, Any]]:
    return [
        {"path": name, "size": len(data)}
        for name, data in files.items()
        if name not in skip
    ]


def _file_list_from_zip(pkg: bytes, skip: set[str]) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(pkg)) as zf:
        return [
            {"path": name, "size": zf.getinfo(name).file_size}
            for name in zf.namelist()
            if not name.endswith("/") and name not in skip
        ]


async def _versions(db: AsyncSession, name: str, kind: str) -> list[Capability]:
    rows = (
        await db.scalars(
            select(Capability)
            .options(selectinload(Capability.artifacts))
            .where(and_(Capability.name == name, Capability.type == kind))
        )
    ).all()
    return list(rows)


async def get_editable(
    db: AsyncSession, user: User | None, name: str, kind: str
) -> tuple[Capability, str, list[dict[str, Any]], str, bool, str]:
    spec = _spec(kind)
    versions = await _versions(db, name, kind)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{spec['label']} {name} 不存在")
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
        cap, published, core_text_files=frozenset({spec["body"]})
    )
    body = text_file(files, spec["body"])
    meta = _meta(files, spec["meta"])
    schema = cap.input_schema if isinstance(cap.input_schema, dict) else {}
    always = bool(meta.get("alwaysApply") or meta.get("always_apply") or schema.get("alwaysApply"))
    globs = meta.get("globs") or schema.get("globs") or ""
    if isinstance(globs, list):
        globs = ",".join(str(g) for g in globs)
    base_version = (
        max(published, key=lambda c: parse_semver(c.version)).version if published else ""
    )
    skip = {spec["meta"], spec["body"]}
    return cap, body, _file_list(files, skip), base_version, always, str(globs)


def build_package(
    *,
    kind: str,
    base: Capability | None,
    name: str,
    description: str,
    version: str,
    body: str,
    category: str,
    tags: list[str],
    always_apply: bool = False,
    globs: str = "",
) -> bytes:
    spec = _spec(kind)
    files: dict[str, bytes] = {}
    base_files = read_capability_files(base) if base is not None else {}
    skip = {spec["meta"], spec["body"]}
    for fname, content in base_files.items():
        if fname in skip:
            continue
        files[fname] = content
    files[spec["body"]] = body.encode("utf-8")
    meta = _meta(base_files, spec["meta"])
    payload = {
        "name": name,
        "description": description or meta.get("description", ""),
        "version": version,
        "category": category or meta.get("category", ""),
        "tags": tags if tags is not None else (meta.get("tags") or []),
    }
    if kind == "rule":
        payload["alwaysApply"] = always_apply
        payload["globs"] = globs
    files[spec["meta"]] = json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
    return buf.getvalue()


async def save_version(
    db: AsyncSession, user: User, name: str, kind: str, data: MarkdownKindEditSave
) -> tuple[Capability, str, list[dict[str, Any]]]:
    spec = _spec(kind)
    versions = await _versions(db, name, kind)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"{spec['label']} {name} 不存在")
    draft = next((c for c in versions if c.status in ("draft", "returned", "rejected")), None)
    published = [c for c in versions if c.status in ("published", "deprecated")]
    base = max(published, key=lambda c: parse_semver(c.version)) if published else None
    inherit = merge_edit_package_files(
        draft or base or versions[0], published, core_text_files=frozenset({spec["body"]})
    )
    inherit_body = text_file(inherit, spec["body"])
    inherit_meta = _meta(inherit, spec["meta"])

    if draft is not None:
        if user.role != "admin" and draft.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限编辑该草稿")
        cap = draft
        body = data.body if str(data.body or "").strip() else inherit_body
    else:
        if base is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"该{spec['label']}没有已发布版本，无法创建新版本",
            )
        if user.role != "admin" and base.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, f"只能编辑自己发布的{spec['label']}")
        new_version = data.new_version or next_version(base.version, "patch")
        exists = await db.scalar(
            select(Capability.id).where(
                and_(
                    Capability.name == name,
                    Capability.version == new_version,
                    Capability.type == kind,
                )
            )
        )
        if exists:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"{spec['label']} {name} 已存在版本 {new_version}"
            )
        cap = Capability(
            name=name,
            description=data.description or base.description,
            type=kind,
            version=new_version,
            category=data.category or base.category,
            tags=data.tags if data.tags is not None else list(base.tags or []),
            visibility=base.visibility,
            status="draft",
            author_id=user.id,
            organization=user.department,
            **draft_policy_kwargs(base),
        )
        db.add(cap)
        await db.flush()
        body = data.body if str(data.body or "").strip() else inherit_body

    if data.description:
        cap.description = data.description
    if data.category:
        cap.category = data.category
    if data.tags is not None:
        cap.tags = list(data.tags)

    always = (
        inherit_meta.get("alwaysApply")
        if data.always_apply is None
        else data.always_apply
    )
    globs = data.globs if data.globs is not None else str(inherit_meta.get("globs") or "")
    pkg = build_package(
        kind=kind,
        base=package_base_for_save(draft, published) or base,
        name=cap.name,
        description=cap.description or "",
        version=cap.version,
        body=body,
        category=cap.category or "",
        tags=list(cap.tags or []),
        always_apply=bool(always),
        globs=str(globs or ""),
    )
    schema = dict(cap.input_schema or {})
    schema["kind"] = kind
    schema["name"] = cap.name
    schema["version"] = cap.version
    schema["description"] = cap.description or ""
    if kind == "rule":
        schema["alwaysApply"] = bool(always)
        schema["globs"] = str(globs or "")
    cap.input_schema = schema
    info = get_storage().save(cap.id, f"{cap.name}-{cap.version}.zip", io.BytesIO(pkg))
    db.add(
        CapabilityArtifact(
            capability_id=cap.id, filename=f"{cap.name}-{cap.version}.zip", **info
        )
    )
    await db.commit()
    skip = {spec["meta"], spec["body"]}
    return cap, body, _file_list_from_zip(pkg, skip)
