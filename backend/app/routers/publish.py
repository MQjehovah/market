"""发布者：草稿管理 / 提交审核 / 版本管理 / 能力包上传下载。"""

import io
import json
import zipfile

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, DbSession
from app.models import Capability
from app.schemas import (
    CapabilityCreate,
    CapabilityOut,
    CapabilityUpdate,
    MessageOut,
    VersionCreate,
)
from app.services.capabilities import (
    DELETABLE_STATUSES,
    EDITABLE_STATUSES,
    UPLOADABLE_STATUSES,
    create_capability,
    create_new_version,
    delete_capability,
    get_visible_capabilities,
    highest_version,
    next_version,
    submit_for_review,
    to_capability_out,
    update_capability,
    withdraw_capability,
)
from app.services.packages import prepare_package
from app.storage import get_storage

router = APIRouter(prefix="/api/publish", tags=["publish"])


def _require_owner(cap: Capability, user) -> None:
    if cap.author_id != user.id and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只能操作自己发布的能力")


def _require_creator(user) -> None:
    """任何登录用户都可创建自己的能力草稿（审核上架仍由管理员把关）。"""
    if user is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "请先登录")


@router.get("/my", response_model=list[CapabilityOut])
async def my_capabilities(db: DbSession, user: CurrentUser):
    caps = (
        await db.scalars(
            select(Capability)
            .options(selectinload(Capability.artifacts), joinedload(Capability.author))
            .where(Capability.author_id == user.id)
        )
    ).all()
    versions = list(caps)
    return [
        to_capability_out(cap, versions=versions, author_name=user.username) for cap in caps
    ]


@router.post("/capabilities", response_model=CapabilityOut, status_code=status.HTTP_201_CREATED)
async def create(data: CapabilityCreate, db: DbSession, user: CurrentUser):
    _require_creator(user)
    cap = await create_capability(db, user, data)
    return to_capability_out(cap, author_name=user.username)


@router.put("/capabilities/{cap_id}", response_model=CapabilityOut)
async def update(cap_id: str, data: CapabilityUpdate, db: DbSession, user: CurrentUser):
    _require_creator(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    if cap.status not in EDITABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "仅草稿、打回或驳回的能力可以编辑")
    cap = await update_capability(db, cap, data)
    cap = await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts), joinedload(Capability.author))
        .where(Capability.id == cap.id)
    )
    return to_capability_out(cap, author_name=user.username)


@router.post("/capabilities/{cap_id}/withdraw", response_model=CapabilityOut)
async def withdraw(cap_id: str, db: DbSession, user: CurrentUser):
    _require_creator(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    cap = await withdraw_capability(db, cap, user)
    return to_capability_out(cap, author_name=user.username)


@router.post("/capabilities/{cap_id}/submit", response_model=CapabilityOut)
async def submit(cap_id: str, db: DbSession, user: CurrentUser):
    _require_creator(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    cap = await submit_for_review(db, cap)
    return to_capability_out(cap, author_name=user.username)


@router.post("/capabilities/{cap_id}/versions", response_model=CapabilityOut, status_code=status.HTTP_201_CREATED)
async def new_version(cap_id: str, data: VersionCreate, db: DbSession, user: CurrentUser):
    _require_creator(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    version = data.new_version or next_version(cap.version, data.change_type)
    new_cap = await create_new_version(
        db, user, cap, version, changelog=data.changelog or ""
    )
    return to_capability_out(new_cap, author_name=user.username)


@router.get("/capabilities/{cap_id}/next-version", response_model=dict)
async def suggest_version(cap_id: str, db: DbSession, user: CurrentUser):
    _require_creator(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    # 相对「同名同类型已有最高版本」建议，避免已有草稿时仍建议冲突号
    base = await highest_version(db, cap.name, cap.type) or cap.version
    return {
        "current": cap.version,
        "base": base,
        "major": next_version(base, "major"),
        "minor": next_version(base, "minor"),
        "patch": next_version(base, "patch"),
    }


@router.post("/capabilities/{cap_id}/artifact", response_model=CapabilityOut)
async def upload_artifact(cap_id: str, db: DbSession, user: CurrentUser, file: UploadFile = File(...)):
    _require_creator(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    if cap.status not in UPLOADABLE_STATUSES:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "仅草稿、打回、驳回或待审状态可上传能力包；已发布请先创建新版本",
        )
    content = await file.read()
    content, details = prepare_package(cap.type, content)
    from app.services.packages import extract_readme_text

    cap.readme_md = extract_readme_text(content)
    files_map = details.get("files") or {}
    cap.validation_report = {
        "ok": not bool(details.get("errors")),
        "warnings": list(details.get("warnings") or []),
        "errors": list(details.get("errors") or []),
        "files": sorted(files_map.keys()) if isinstance(files_map, dict) else list(files_map),
        "meta": {k: details.get(k) for k in ("meta", "connection", "schema") if k in details and not isinstance(details.get(k), (bytes, bytearray))},
    }
    # strip heavy blobs from meta
    meta = cap.validation_report.get("meta") or {}
    for k, v in list(meta.items()):
        if isinstance(v, dict):
            meta[k] = {kk: vv for kk, vv in v.items() if not isinstance(vv, (bytes, bytearray))}
    if cap.type == "tool" and details.get("schema"):
        cap.input_schema = details["schema"]
    if cap.type == "agent":
        import zipfile

        from app.services.agent_metadata import enrich_embedded_with_market, extract_agent_embedded

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            logical = {logical: zf.read(raw) for logical, raw in details["files"].items()}
        embedded = extract_agent_embedded(logical)
        # 保留既有非嵌入字段，仅刷新 embedded_*；上传时固化市场关联
        schema = dict(cap.input_schema or {})
        schema["embedded_skills"] = embedded["embedded_skills"]
        schema["embedded_mcp"] = embedded["embedded_mcp"]
        cap.input_schema = await enrich_embedded_with_market(db, schema)
    if cap.type == "skill" and details.get("meta"):
        meta = details["meta"]
        identity = meta.get("identity") if isinstance(meta.get("identity"), dict) else {}
        cap.input_schema = {
            "kind": "skill",
            "name": meta.get("name") or identity.get("name") or cap.name,
            "version": meta.get("version") or identity.get("version") or cap.version,
            "description": meta.get("description") or identity.get("description") or "",
            "category": meta.get("category") or identity.get("category") or cap.category or "",
            "display_name": identity.get("display_name") or meta.get("display_name") or "",
        }
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
    if cap.type == "plugin":
        from app.services.plugins import materialize_plugin_components

        await materialize_plugin_components(db, user, cap, content, details)
    info = get_storage().save(cap.id, file.filename or "package.zip", io.BytesIO(content))
    from app.models import CapabilityArtifact

    artifact = CapabilityArtifact(capability_id=cap.id, **info, filename=file.filename or "package.zip")
    db.add(artifact)
    await db.commit()
    cap = await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts), joinedload(Capability.author))
        .where(Capability.id == cap.id)
    )
    return to_capability_out(cap, author_name=user.username)


@router.get("/capabilities/{cap_id}/artifact/download")
async def download_artifact(cap_id: str, db: DbSession, user: CurrentUser):
    _require_creator(user)
    cap = await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts))
        .where(Capability.id == cap_id)
    )
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    if not cap.artifacts:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该能力尚未上传能力包")
    artifact = cap.artifacts[-1]
    content = get_storage().open(artifact.uri)
    return StreamingResponse(content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'})


@router.delete("/capabilities/{cap_id}", response_model=MessageOut)
async def remove_capability(cap_id: str, db: DbSession, user: CurrentUser):
    _require_creator(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    if cap.status not in DELETABLE_STATUSES:
        raise HTTPException(status.HTTP_409_CONFLICT, "仅草稿、待审、驳回或打回状态的能力可以删除")
    await delete_capability(db, cap)
    return MessageOut(message="已删除")
