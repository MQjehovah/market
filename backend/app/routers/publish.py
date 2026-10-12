"""发布者：草稿管理 / 提交审核 / 版本管理 / 能力包上传下载。"""

import io
import json
import zipfile
from urllib.parse import quote

from fastapi import APIRouter, File, Form, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, DbSession
from app.models import Capability, CapabilityArtifact, CapabilityCollaborator, User
from app.schemas import (
    CapabilityCreate,
    CapabilityOut,
    CapabilityUpdate,
    CollaboratorAdd,
    MessageOut,
    PackageEditSave,
    TransferOwnershipRequest,
    UserBriefOut,
    VersionCreate,
)
from app.services.capabilities import (
    DELETABLE_STATUSES,
    EDITABLE_STATUSES,
    UPLOADABLE_STATUSES,
    create_capability,
    create_new_version,
    delete_capability,
    highest_version,
    next_version,
    parse_semver,
    read_capability_files,
    submit_for_review,
    to_capability_out,
    update_capability,
    withdraw_capability,
)
from app.services.capability_package import apply_package
from app.services.collaboration import (
    add_collaborator,
    is_collaborator,
    list_collaborators,
    notify,
    remove_collaborator,
)
from app.services.packages import prepare_package
from app.storage import get_storage

router = APIRouter(prefix="/api/publish", tags=["publish"])


def _require_owner(cap: Capability, user) -> None:
    if cap.author_id != user.id and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只能操作自己发布的能力")


async def _is_cap_editor(db, cap: Capability, user) -> bool:
    """内容编辑权：作者 / 管理员 / 协作者。"""
    if user.role == "admin" or cap.author_id == user.id:
        return True
    return await is_collaborator(db, cap.name, user.id)


async def _require_cap_editor(db, cap: Capability, user) -> None:
    if not await _is_cap_editor(db, cap, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只能操作自己创建或已协作的能力")


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
    await _require_cap_editor(db, cap, user)
    # 协作者不可修改「名称/类型」（身份变更仍仅限作者/管理员）
    if user.role != "admin" and cap.author_id != user.id:
        if (data.name is not None and data.name != cap.name) or (
            data.type is not None and str(data.type) != cap.type
        ):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "协作者不能修改能力名称/类型")
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
    await _require_cap_editor(db, cap, user)
    cap = await withdraw_capability(db, cap, user)
    return to_capability_out(cap, author_name=user.username)


@router.post("/capabilities/{cap_id}/submit", response_model=CapabilityOut)
async def submit(cap_id: str, db: DbSession, user: CurrentUser):
    _require_creator(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    await _require_cap_editor(db, cap, user)
    cap = await submit_for_review(db, cap)
    return to_capability_out(cap, author_name=user.username)


@router.post("/capabilities/{cap_id}/versions", response_model=CapabilityOut, status_code=status.HTTP_201_CREATED)
async def new_version(cap_id: str, data: VersionCreate, db: DbSession, user: CurrentUser):
    _require_creator(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    await _require_cap_editor(db, cap, user)
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
    await _require_cap_editor(db, cap, user)
    # 相对「同名同类型已有最高版本」建议，避免已有草稿时仍建议冲突号
    base = await highest_version(db, cap.name, cap.type) or cap.version
    return {
        "current": cap.version,
        "base": base,
        "major": next_version(base, "major"),
        "minor": next_version(base, "minor"),
        "patch": next_version(base, "patch"),
    }


async def _find_open_draft(db, cap: Capability) -> Capability | None:
    rows = (
        await db.scalars(
            select(Capability)
            .options(selectinload(Capability.artifacts))
            .where(and_(Capability.name == cap.name, Capability.type == cap.type))
        )
    ).all()
    drafts = [r for r in rows if r.status in ("draft", "returned", "rejected")]
    return max(drafts, key=lambda c: parse_semver(c.version)) if drafts else None


async def _latest_published_with_artifact(db, cap: Capability) -> Capability | None:
    rows = (
        await db.scalars(
            select(Capability)
            .options(selectinload(Capability.artifacts))
            .where(and_(Capability.name == cap.name, Capability.type == cap.type))
        )
    ).all()
    pubs = [r for r in rows if r.status in ("published", "deprecated") and r.artifacts]
    return max(pubs, key=lambda c: parse_semver(c.version)) if pubs else None


def _inherit_package(db, src: Capability, dst: Capability) -> None:
    """把 src 的能力包复制到 dst(草稿)，并继承 README/schema/校验摘要。"""
    if not src.artifacts:
        return
    content = get_storage().open(src.artifacts[-1].uri).read()
    info = get_storage().save(dst.id, f"{dst.name}-{dst.version}.zip", io.BytesIO(content))
    db.add(CapabilityArtifact(capability_id=dst.id, filename=f"{dst.name}-{dst.version}.zip", **info))
    dst.readme_md = src.readme_md or dst.readme_md or ""
    if src.input_schema:
        dst.input_schema = dict(src.input_schema)
    if src.validation_report:
        dst.validation_report = dict(src.validation_report)


async def _reload_cap(db, cap_id: str) -> Capability:
    return await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts), joinedload(Capability.author))
        .where(Capability.id == cap_id)
    )


async def ensure_editable_draft(db, user, cap: Capability) -> Capability:
    """作者/协作者/管理员返回可编辑的草稿（已发布则自动开新版并继承能力包）。

    - 已是可上传态(草稿/打回/驳回/待审) → 原样返回；
    - 已发布/已下架 → 复用同名草稿，否则基于已发布新建下一版本草稿并复制能力包。
    """
    await _require_cap_editor(db, cap, user)
    if cap.status in UPLOADABLE_STATUSES:
        return await _reload_cap(db, cap.id)
    if cap.status not in ("published", "deprecated"):
        hint = "；审核中的能力请先撤回后再修改" if cap.status == "reviewing" else ""
        raise HTTPException(status.HTTP_409_CONFLICT, f"当前状态不可在线编辑{hint}")
    draft = await _find_open_draft(db, cap)
    if draft is not None:
        return await _reload_cap(db, draft.id)
    base = cap if cap.artifacts else await _latest_published_with_artifact(db, cap)
    version = next_version(cap.version, "patch")
    while await db.scalar(
        select(Capability.id).where(
            and_(Capability.name == cap.name, Capability.version == version)
        )
    ):
        version = next_version(version, "patch")
    draft = await create_new_version(
        db, user, cap, version, changelog=f"基于 v{cap.version} 在线编辑（自动开新版）"
    )
    if base is not None and base.artifacts:
        _inherit_package(db, base, draft)
        await db.commit()
    return await _reload_cap(db, draft.id)


@router.post("/capabilities/{cap_id}/edit-draft", response_model=CapabilityOut)
async def edit_draft(cap_id: str, db: DbSession, user: CurrentUser):
    """一键基于已发布版本开启可编辑草稿（继承能力包）；属主或管理员可用。"""
    _require_creator(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    draft = await ensure_editable_draft(db, user, cap)
    return to_capability_out(draft, author_name=user.username)


@router.post("/capabilities/{cap_id}/artifact", response_model=CapabilityOut)
async def upload_artifact(cap_id: str, db: DbSession, user: CurrentUser, file: UploadFile = File(...)):
    _require_creator(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    # 已发布/已下架：属主或管理员上传即自动开新版（继承已发布能力包）
    cap = await ensure_editable_draft(db, user, cap)
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
        from app.services.agent_metadata import enrich_embedded_with_market, extract_agent_embedded

        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            logical = {logical: zf.read(raw) for logical, raw in details["files"].items()}
        embedded = extract_agent_embedded(logical)
        # 保留既有非嵌入字段，仅刷新 embedded_*；上传时固化市场关联
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
    info = get_storage().save(cap.id, file.filename or "package.zip", io.BytesIO(content))
    from app.models import CapabilityArtifact

    artifact = CapabilityArtifact(capability_id=cap.id, **info, filename=file.filename or "package.zip")
    db.add(artifact)
    await db.commit()
    cap = await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts), joinedload(Capability.author))
        .where(Capability.id == cap.id)
        .execution_options(populate_existing=True)
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
    filename = getattr(artifact, "filename", "") or "package.zip"
    # 文件名可能含中文：用 RFC 5987 filename*，避免 latin-1 头编码报错
    return StreamingResponse(
        content,
        media_type="application/zip",
        headers={"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"},
    )


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


def _norm_pkg_path(raw: str) -> str:
    path = str(raw or "").strip().replace("\\", "/").lstrip("/")
    if not path or path.endswith("/"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "请提供包内文件路径")
    if ".." in path.split("/"):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"非法路径：{raw}")
    return path


def _zip_files(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(path, content)
    return buf.getvalue()


@router.put("/capabilities/{cap_id}/package", response_model=CapabilityOut)
async def edit_package(cap_id: str, data: PackageEditSave, db: DbSession, user: CurrentUser):
    """在线编辑：按文件树覆盖文本文件 / 删除文件，重建能力包。"""
    _require_creator(user)
    cap = await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts))
        .where(Capability.id == cap_id)
    )
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    # 已发布/已下架：属主或管理员编辑文件即自动开新版（继承已发布能力包）
    cap = await ensure_editable_draft(db, user, cap)

    files = read_capability_files(cap)
    for raw in data.deleted or []:
        files.pop(_norm_pkg_path(raw), None)
    for item in data.files or []:
        path = _norm_pkg_path(item.path)
        files[path] = (item.content or "").encode("utf-8")
    if not files:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "能力包为空，请至少保留一个文件")

    cap = await apply_package(db, user, cap, _zip_files(files))
    return to_capability_out(cap, author_name=user.username)


@router.post("/capabilities/{cap_id}/package/file", response_model=CapabilityOut)
async def upload_package_file(
    cap_id: str,
    db: DbSession,
    user: CurrentUser,
    file: UploadFile = File(...),
    path: str = Form(""),
):
    """直接上传单个文件到能力包（文本或二进制均可）。"""
    _require_creator(user)
    cap = await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts))
        .where(Capability.id == cap_id)
    )
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    # 已发布/已下架：属主或管理员上传文件即自动开新版（继承已发布能力包）
    cap = await ensure_editable_draft(db, user, cap)

    target = _norm_pkg_path(path or file.filename or "file.bin")
    files = read_capability_files(cap)
    files[target] = await file.read()

    cap = await apply_package(db, user, cap, _zip_files(files))
    return to_capability_out(cap, author_name=user.username)


# ---------------------------------------------------------------- 转交 / 协作

@router.get("/users/search", response_model=list[UserBriefOut])
async def search_users(db: DbSession, user: CurrentUser, q: str = ""):
    """按 用户名/姓名/工号 模糊搜索可协作用户（登录即可用，最多 10 条）。"""
    keyword = (q or "").strip()
    if len(keyword) < 1:
        return []
    like = f"%{keyword}%"
    rows = (
        await db.scalars(
            select(User)
            .where(
                User.is_active.is_(True),
                or_(User.username.like(like), User.name.like(like), User.work_id.like(like)),
            )
            .order_by(User.username)
            .limit(10)
        )
    ).all()
    return [
        UserBriefOut(id=u.id, username=u.username, name=u.name or "", department=u.department or "")
        for u in rows
    ]


async def _get_cap_or_404(db, cap_id: str) -> Capability:
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    return cap


def _require_manage(cap: Capability, user) -> None:
    """管理权（协作名单/转交）：仅作者或管理员。"""
    if user.role != "admin" and cap.author_id != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅作者或管理员可执行该操作")


@router.get("/capabilities/{cap_id}/access")
async def capability_access(cap_id: str, db: DbSession, user: CurrentUser):
    """当前登录用户对该能力的权限标记（详情页/编辑器按钮收敛用）。"""
    cap = await _get_cap_or_404(db, cap_id)
    is_admin = user.role == "admin"
    is_author = cap.author_id == user.id
    is_collab = False if (is_admin or is_author) else await is_collaborator(db, cap.name, user.id)
    return {
        "is_admin": is_admin,
        "is_author": is_author,
        "is_collaborator": is_collab,
        "can_edit": bool(is_admin or is_author or is_collab),
        "can_manage": bool(is_admin or is_author),
    }


@router.get("/collaborations", response_model=list[CapabilityOut])
async def my_collaborations(db: DbSession, user: CurrentUser):
    """我作为协作者的能力列表（按能力名取代表版本：优先已发布最高版）。"""
    names = (
        await db.scalars(
            select(CapabilityCollaborator.capability_name).where(
                CapabilityCollaborator.user_id == user.id
            )
        )
    ).all()
    out: list[CapabilityOut] = []
    for name in dict.fromkeys(names):
        rows = (
            await db.scalars(
                select(Capability)
                .options(selectinload(Capability.artifacts), joinedload(Capability.author))
                .where(Capability.name == name)
            )
        ).all()
        if not rows:
            continue
        pubs = [r for r in rows if r.status in ("published", "deprecated")]
        pick = max(pubs, key=lambda c: parse_semver(c.version)) if pubs else max(
            rows, key=lambda c: parse_semver(c.version)
        )
        out.append(
            to_capability_out(
                pick, versions=list(rows), author_name=pick.author.username if pick.author else ""
            )
        )
    return out


@router.get("/capabilities/{cap_id}/collaborators")
async def capability_collaborators(cap_id: str, db: DbSession, user: CurrentUser):
    """协作者名单（作者/管理员/协作者可查看）。"""
    cap = await _get_cap_or_404(db, cap_id)
    await _require_cap_editor(db, cap, user)
    return {"name": cap.name, "items": await list_collaborators(db, cap.name)}


@router.post("/capabilities/{cap_id}/collaborators")
async def add_capability_collaborator(
    cap_id: str, data: CollaboratorAdd, db: DbSession, user: CurrentUser
):
    """添加协作者（仅作者/管理员）。"""
    cap = await _get_cap_or_404(db, cap_id)
    _require_manage(cap, user)
    target = await db.get(User, data.user_id)
    if target is None or not target.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "目标用户不存在或已停用")
    if target.id == cap.author_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "该用户已是能力作者")
    await add_collaborator(db, cap.name, target, user, link=f"/capabilities/{cap.id}")
    await db.commit()
    return {"name": cap.name, "items": await list_collaborators(db, cap.name)}


@router.delete("/capabilities/{cap_id}/collaborators/{user_id}")
async def remove_capability_collaborator(
    cap_id: str, user_id: str, db: DbSession, user: CurrentUser
):
    """移除协作者（仅作者/管理员）。"""
    cap = await _get_cap_or_404(db, cap_id)
    _require_manage(cap, user)
    removed = await remove_collaborator(db, cap.name, user_id)
    if removed:
        target = await db.get(User, user_id)
        await notify(
            db,
            user_id,
            f"你已被移出能力「{cap.name}」的协作者",
            f"{user.name or user.username} 移除了你的协作权限",
            link=f"/capabilities/{cap.id}",
        )
    await db.commit()
    return {"name": cap.name, "removed": removed, "items": await list_collaborators(db, cap.name)}


@router.post("/capabilities/{cap_id}/transfer")
async def transfer_capability(
    cap_id: str, data: TransferOwnershipRequest, db: DbSession, user: CurrentUser
):
    """转交能力归属：同名全部版本一起转给目标用户（仅作者/管理员），并通知双方。"""
    cap = await _get_cap_or_404(db, cap_id)
    _require_manage(cap, user)
    target = await db.get(User, data.to_user_id)
    if target is None or not target.is_active:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "目标用户不存在或已停用")
    if target.id == cap.author_id:
        raise HTTPException(status.HTTP_409_CONFLICT, "该用户已是能力作者")
    rows = (
        await db.scalars(select(Capability).where(Capability.name == cap.name))
    ).all()
    old_author_id = cap.author_id
    for row in rows:
        row.author_id = target.id
    await notify(
        db,
        target.id,
        f"能力「{cap.name}」已转交给你",
        f"{user.name or user.username} 将《{cap.name}》（全部版本，共 {len(rows)} 个）的归属转交给你",
        link=f"/capabilities/{cap.id}",
    )
    if old_author_id and old_author_id != user.id:
        await notify(
            db,
            old_author_id,
            f"能力「{cap.name}」已转交给 {target.name or target.username}",
            f"操作人：{user.name or user.username}",
            link=f"/capabilities/{cap.id}",
        )
    await db.commit()
    return {"name": cap.name, "to": target.username, "versions": len(rows)}
