"""发布者：草稿管理 / 提交审核 / 版本管理 / 能力包上传下载。"""

import io

from fastapi import APIRouter, File, HTTPException, UploadFile, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, DbSession
from app.cache import invalidate_marketplace_cache
from app.models import Capability
from app.schemas import (
    CapabilityCreate,
    CapabilityOut,
    CapabilityUpdate,
    MessageOut,
    VersionCreate,
)
from app.services.capabilities import (
    create_capability,
    create_new_version,
    get_visible_capabilities,
    next_version,
    submit_for_review,
    to_capability_out,
    update_capability,
)
from app.services.packages import validate_package
from app.storage import get_storage

router = APIRouter(prefix="/api/publish", tags=["publish"])


def _require_owner(cap: Capability, user) -> None:
    if cap.author_id != user.id and user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "只能操作自己发布的能力")


def _require_publisher(user) -> None:
    if user.role not in ("admin", "publisher"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "普通用户无权发布能力，请联系管理员开通发布权限")


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
    out = []
    for cap in caps:
        o = to_capability_out(cap, versions=versions, author_name=user.username)
        o.latest = cap == max(versions, key=lambda c: (int(c.version.split(".")[0]), int(c.version.split(".")[1]), int(c.version.split(".")[2])))
        out.append(o)
    return out


@router.post("/capabilities", response_model=CapabilityOut, status_code=status.HTTP_201_CREATED)
async def create(data: CapabilityCreate, db: DbSession, user: CurrentUser):
    _require_publisher(user)
    cap = await create_capability(db, user, data)
    return to_capability_out(cap, author_name=user.username)


@router.put("/capabilities/{cap_id}", response_model=CapabilityOut)
async def update(cap_id: str, data: CapabilityUpdate, db: DbSession, user: CurrentUser):
    _require_publisher(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    if cap.status not in ("draft", "returned"):
        raise HTTPException(status.HTTP_409_CONFLICT, "仅草稿或被打回的能力可以编辑")
    cap = await update_capability(db, cap, data)
    return to_capability_out(cap, author_name=user.username)


@router.post("/capabilities/{cap_id}/submit", response_model=CapabilityOut)
async def submit(cap_id: str, db: DbSession, user: CurrentUser):
    _require_publisher(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    cap = await submit_for_review(db, cap)
    return to_capability_out(cap, author_name=user.username)


@router.post("/capabilities/{cap_id}/versions", response_model=CapabilityOut, status_code=status.HTTP_201_CREATED)
async def new_version(cap_id: str, data: VersionCreate, db: DbSession, user: CurrentUser):
    _require_publisher(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    new_cap = await create_new_version(db, user, cap, data.new_version)
    return to_capability_out(new_cap, author_name=user.username)


@router.get("/capabilities/{cap_id}/next-version", response_model=dict)
async def suggest_version(cap_id: str, db: DbSession, user: CurrentUser):
    _require_publisher(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    return {
        "current": cap.version,
        "major": next_version(cap.version, "major"),
        "minor": next_version(cap.version, "minor"),
        "patch": next_version(cap.version, "patch"),
    }


@router.post("/capabilities/{cap_id}/artifact", response_model=CapabilityOut)
async def upload_artifact(cap_id: str, db: DbSession, user: CurrentUser, file: UploadFile = File(...)):
    _require_publisher(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    content = await file.read()
    details = validate_package(cap.type, content)
    if cap.type == "tool" and details.get("schema"):
        cap.input_schema = details["schema"]
    info = get_storage().save(cap.id, file.filename or "package.zip", io.BytesIO(content))
    from app.models import CapabilityArtifact

    artifact = CapabilityArtifact(capability_id=cap.id, **info, filename=file.filename or "package.zip")
    db.add(artifact)
    await db.commit()
    await db.refresh(cap)
    invalidate_marketplace_cache()
    return to_capability_out(cap, author_name=user.username)


@router.get("/capabilities/{cap_id}/artifact/download")
async def download_artifact(cap_id: str, db: DbSession, user: CurrentUser):
    _require_publisher(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    if not cap.artifacts:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该能力尚未上传能力包")
    artifact = cap.artifacts[-1]
    content = get_storage().open(artifact.uri)
    return StreamingResponse(content, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="{artifact.filename}"'})


@router.delete("/capabilities/{cap_id}", response_model=MessageOut)
async def delete_capability(cap_id: str, db: DbSession, user: CurrentUser):
    _require_publisher(user)
    cap = await db.get(Capability, cap_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    _require_owner(cap, user)
    if cap.status not in ("draft", "rejected", "returned"):
        raise HTTPException(status.HTTP_409_CONFLICT, "仅草稿、驳回或打回状态的能力可以删除")
    await db.delete(cap)
    await db.commit()
    invalidate_marketplace_cache()
    return MessageOut(message="已删除")
