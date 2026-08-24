"""工具在线编辑：读取 / 保存 schema.json 与 implementation/tool.py（保存即新版本草稿）。"""

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
from app.schemas import ToolEditSave
from app.services.capabilities import next_version, parse_semver
from app.services.packages import validate_tool_schema
from app.storage import get_storage

_CORE = {"tool.json", "schema.json", "implementation/tool.py"}


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


def _loads(raw: bytes | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


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


async def _tool_versions(db: AsyncSession, name: str) -> list[Capability]:
    rows = (
        await db.scalars(
            select(Capability)
            .options(selectinload(Capability.artifacts))
            .where(and_(Capability.name == name, Capability.type == "tool"))
        )
    ).all()
    return list(rows)


async def get_editable(
    db: AsyncSession, user: User | None, name: str
) -> tuple[Capability, dict[str, Any], str, list[dict[str, Any]], str]:
    versions = await _tool_versions(db, name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"工具 {name} 不存在")
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
    files = _read_zip(cap) or {}
    schema = _loads(files.get("schema.json"), {})
    if not isinstance(schema, dict):
        schema = {}
    # Prefer denormalized input_schema when package schema missing
    if not schema and isinstance(cap.input_schema, dict) and cap.input_schema.get("properties"):
        schema = dict(cap.input_schema)
    impl = files.get("implementation/tool.py", b"").decode("utf-8", errors="replace")
    base_version = (
        max(published, key=lambda c: parse_semver(c.version)).version if published else ""
    )
    return cap, schema, impl, _file_list(files), base_version


def build_tool_package(
    *,
    base: Capability | None,
    name: str,
    description: str,
    version: str,
    schema: dict[str, Any],
    implementation: str,
    category: str,
    tags: list[str],
) -> bytes:
    files: dict[str, bytes] = {}
    base_files = _read_zip(base) if base is not None else None
    if base_files:
        for fname, content in base_files.items():
            if fname in _CORE:
                continue
            files[fname] = content
    meta = _loads((base_files or {}).get("tool.json"), {}) or {}
    files["tool.json"] = json.dumps(
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
    files["schema.json"] = json.dumps(schema, ensure_ascii=False, indent=2).encode("utf-8")
    code = implementation if implementation.strip() else (
        (base_files or {}).get("implementation/tool.py", b"def run(params):\n    return {'ok': True}\n").decode(
            "utf-8", errors="replace"
        )
    )
    files["implementation/tool.py"] = code.encode("utf-8")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
    return buf.getvalue()


async def save_version(
    db: AsyncSession, user: User, name: str, data: ToolEditSave
) -> tuple[Capability, dict[str, Any], str, list[dict[str, Any]]]:
    versions = await _tool_versions(db, name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"工具 {name} 不存在")
    draft = next((c for c in versions if c.status in ("draft", "returned", "rejected")), None)
    published = [c for c in versions if c.status in ("published", "deprecated")]
    base = max(published, key=lambda c: parse_semver(c.version)) if published else None

    schema = validate_tool_schema(data.tool_schema or {}, "schema.json")

    if draft is not None:
        if user.role != "admin" and draft.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限编辑该草稿")
        cap = draft
    else:
        if base is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "该工具没有已发布版本，无法创建新版本",
            )
        if user.role != "admin" and base.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "只能编辑自己发布的工具")
        new_version = data.new_version or next_version(base.version, "patch")
        exists = await db.scalar(
            select(Capability.id).where(
                and_(
                    Capability.name == name,
                    Capability.version == new_version,
                    Capability.type == "tool",
                )
            )
        )
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, f"工具 {name} 已存在版本 {new_version}")
        cap = Capability(
            name=name,
            description=data.description or base.description,
            type="tool",
            version=new_version,
            category=data.category or base.category,
            tags=data.tags if data.tags is not None else list(base.tags or []),
            visibility=base.visibility,
            access_policy=base.access_policy,
            allowed_users=list(base.allowed_users or []),
            status="draft",
            author_id=user.id,
            organization=user.organization,
        )
        db.add(cap)
        await db.flush()

    if data.description:
        cap.description = data.description
    if data.category:
        cap.category = data.category
    if data.tags is not None:
        cap.tags = list(data.tags)

    pkg = build_tool_package(
        base=base or draft,
        name=cap.name,
        description=cap.description or "",
        version=cap.version,
        schema=schema,
        implementation=data.implementation,
        category=cap.category or "",
        tags=list(cap.tags or []),
    )
    cap.input_schema = schema
    info = get_storage().save(cap.id, f"{cap.name}-{cap.version}.zip", io.BytesIO(pkg))
    db.add(
        CapabilityArtifact(
            capability_id=cap.id, filename=f"{cap.name}-{cap.version}.zip", **info
        )
    )
    await db.commit()
    impl = data.implementation or ""
    if not impl.strip():
        with zipfile.ZipFile(io.BytesIO(pkg)) as zf:
            impl = zf.read("implementation/tool.py").decode("utf-8", errors="replace")
    return cap, schema, impl, _file_list_from_zip(pkg)
