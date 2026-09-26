"""Hook 在线编辑：读取 / 保存 hooks.json（保存即新版本草稿，scripts/ 保留）。"""

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
from app.schemas import HookEditSave
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

_CORE = frozenset({"hooks.json", "hook.json"})


def _meta(files: dict[str, bytes] | None) -> dict[str, Any]:
    try:
        return json.loads((files or {}).get("hook.json", b"{}").decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return {}


def _file_list(files: dict[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": name, "size": len(data)}
        for name, data in files.items()
        if name not in _CORE
    ]


def _file_list_from_zip(pkg: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(pkg)) as zf:
        return [
            {"path": name, "size": zf.getinfo(name).file_size}
            for name in zf.namelist()
            if not name.endswith("/") and name not in _CORE
        ]


def _pretty_hooks(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return json.dumps({"version": 1, "hooks": {}}, ensure_ascii=False, indent=2)
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return text
    return json.dumps(obj, ensure_ascii=False, indent=2)


async def _versions(db: AsyncSession, name: str) -> list[Capability]:
    rows = (
        await db.scalars(
            select(Capability)
            .options(selectinload(Capability.artifacts))
            .where(and_(Capability.name == name, Capability.type == "hook"))
        )
    ).all()
    return list(rows)


async def get_editable(
    db: AsyncSession, user: User | None, name: str
) -> tuple[Capability, str, list[dict[str, Any]], str]:
    versions = await _versions(db, name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Hooks {name} 不存在")
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
    files = merge_edit_package_files(cap, published, core_text_files=_CORE)
    hooks_json = _pretty_hooks(text_file(files, "hooks.json"))
    base_version = (
        max(published, key=lambda c: parse_semver(c.version)).version if published else ""
    )
    return cap, hooks_json, _file_list(files), base_version


def build_hook_package(
    *,
    base: Capability | None,
    name: str,
    description: str,
    version: str,
    hooks_json: str,
    category: str,
    tags: list[str],
) -> bytes:
    try:
        cfg = json.loads(hooks_json or "{}")
    except json.JSONDecodeError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"hooks.json 不是合法 JSON：{exc}") from exc
    if not isinstance(cfg, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "hooks.json 必须是 JSON 对象")
    if "hooks" not in cfg and "version" not in cfg:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "hooks.json 需包含 hooks 对象（Cursor hooks 格式）",
        )
    files: dict[str, bytes] = {}
    base_files = read_capability_files(base) if base is not None else {}
    for fname, content in base_files.items():
        if fname in _CORE:
            continue
        files[fname] = content
    meta = _meta(base_files)
    files["hooks.json"] = json.dumps(cfg, ensure_ascii=False, indent=2).encode("utf-8")
    files["hook.json"] = json.dumps(
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
    db: AsyncSession, user: User, name: str, data: HookEditSave
) -> tuple[Capability, str, list[dict[str, Any]]]:
    versions = await _versions(db, name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"Hooks {name} 不存在")
    draft = next((c for c in versions if c.status in ("draft", "returned", "rejected")), None)
    published = [c for c in versions if c.status in ("published", "deprecated")]
    base = max(published, key=lambda c: parse_semver(c.version)) if published else None
    inherit = merge_edit_package_files(
        draft or base or versions[0], published, core_text_files=_CORE
    )
    inherit_hooks = text_file(inherit, "hooks.json")

    if draft is not None:
        if user.role != "admin" and draft.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限编辑该草稿")
        cap = draft
        hooks_json = data.hooks_json if str(data.hooks_json or "").strip() else inherit_hooks
    else:
        if base is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "该 Hooks 没有已发布版本，无法创建新版本",
            )
        if user.role != "admin" and base.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "只能编辑自己发布的 Hooks")
        new_version = data.new_version or next_version(base.version, "patch")
        exists = await db.scalar(
            select(Capability.id).where(
                and_(
                    Capability.name == name,
                    Capability.version == new_version,
                    Capability.type == "hook",
                )
            )
        )
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, f"Hooks {name} 已存在版本 {new_version}")
        cap = Capability(
            name=name,
            description=data.description or base.description,
            type="hook",
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
        hooks_json = data.hooks_json if str(data.hooks_json or "").strip() else inherit_hooks

    if data.description:
        cap.description = data.description
    if data.category:
        cap.category = data.category
    if data.tags is not None:
        cap.tags = list(data.tags)

    pkg = build_hook_package(
        base=package_base_for_save(draft, published) or base,
        name=cap.name,
        description=cap.description or "",
        version=cap.version,
        hooks_json=hooks_json,
        category=cap.category or "",
        tags=list(cap.tags or []),
    )
    try:
        events = json.loads(hooks_json).get("hooks") or {}
    except json.JSONDecodeError:
        events = {}
    cap.input_schema = {
        "kind": "hook",
        "name": cap.name,
        "version": cap.version,
        "description": cap.description or "",
        "events": sorted(str(k) for k in events.keys()) if isinstance(events, dict) else [],
    }
    info = get_storage().save(cap.id, f"{cap.name}-{cap.version}.zip", io.BytesIO(pkg))
    db.add(
        CapabilityArtifact(
            capability_id=cap.id, filename=f"{cap.name}-{cap.version}.zip", **info
        )
    )
    await db.commit()
    return cap, _pretty_hooks(hooks_json), _file_list_from_zip(pkg)
