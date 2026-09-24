"""能力生命周期：草稿 → 提交审核 → 审核中 → 通过(发布)/驳回/打回 → 弃用 → 归档。"""

import io
import json
import logging
import re
import zipfile
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, delete as sql_delete, func, select
from sqlalchemy.orm import joinedload, selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    A2ATask,
    AgentBinding,
    Capability,
    CapabilityArtifact,
    Notification,
    Rating,
    Review,
    Subscription,
    UsageEvent,
    User,
    UserCapability,
    WorkflowExecution,
)
from app.storage import get_storage
from app.schemas import ArtifactOut, CapabilityCreate, CapabilityOut, CapabilityUpdate
from app.services.visibility import is_capability_visible

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")


def split_cap_ref(ref: str) -> tuple[str, str | None]:
    """解析 name 或 name@version；版本须为合法 semver。"""
    raw = (ref or "").strip()
    if not raw:
        return "", None
    if "@" not in raw:
        return raw, None
    name, _, ver = raw.rpartition("@")
    name, ver = name.strip(), ver.strip()
    if name and ver and SEMVER_RE.match(ver):
        return name, ver
    return raw, None


STATUS_FLOW: dict[str, set[str]] = {
    "draft": {"reviewing"},
    "reviewing": {"published", "rejected", "returned", "draft"},
    "rejected": {"reviewing", "archived"},
    "returned": {"reviewing", "archived"},
    "published": {"deprecated", "archived"},
    "deprecated": {"archived", "published"},
    "archived": set(),
}

EDITABLE_STATUSES = {"draft", "returned", "rejected"}
DELETABLE_STATUSES = {"draft", "returned", "rejected", "reviewing"}
UPLOADABLE_STATUSES = EDITABLE_STATUSES | {"reviewing"}
# workflow 内容在画布维护，提交审核不强制 zip
PACKAGE_REQUIRED_TYPES = {"agent", "tool", "skill", "mcp", "plugin"}


def parse_semver(version: str) -> tuple[int, int, int]:
    m = SEMVER_RE.match(version)
    if not m:
        raise ValueError(f"非法语义化版本号：{version}")
    return tuple(int(x) for x in m.groups())  # type: ignore[return-value]


def next_version(version: str, change_type: str) -> str:
    major, minor, patch = parse_semver(version)
    if change_type == "major":
        return f"{major + 1}.0.0"
    if change_type == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


async def highest_version(
    db: AsyncSession, name: str, type_: str | None = None
) -> str | None:
    """同名（可选同 type）能力中已有的最高语义化版本号。"""
    stmt = select(Capability.version).where(Capability.name == name)
    if type_ is not None:
        stmt = stmt.where(Capability.type == type_)
    versions = list(await db.scalars(stmt))
    if not versions:
        return None
    return max(versions, key=parse_semver)


def is_latest(cap: Capability, versions: list[Capability]) -> bool:
    same = [v for v in versions if v.name == cap.name and v.type == cap.type]
    if not same:
        return True
    return cap == max(same, key=lambda c: parse_semver(c.version))


def editable_content_source(cap: Capability, published: list[Capability]) -> Capability:
    """在线编辑内容来源：当前行已有能力包则用它；新版本空草稿则继承最新已发布包。"""
    if cap.artifacts:
        return cap
    with_pkg = [c for c in published if c.artifacts]
    if not with_pkg:
        return cap
    return max(with_pkg, key=lambda c: parse_semver(c.version))


def read_capability_files(cap: Capability | None) -> dict[str, bytes]:
    """读取能力包内文件；无包或失败时返回空 dict（失败会打日志）。"""
    if cap is None or not cap.artifacts:
        return {}
    try:
        content = get_storage().open(cap.artifacts[-1].uri).read()
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return {
                name: zf.read(name)
                for name in zf.namelist()
                if not name.endswith("/")
            }
    except Exception as exc:  # noqa: BLE001
        logging.getLogger("market.capabilities").warning(
            "读取能力包失败 name=%s uri=%s: %s",
            getattr(cap, "name", "?"),
            cap.artifacts[-1].uri if cap.artifacts else "",
            exc,
        )
        return {}


def latest_published_with_package(published: list[Capability]) -> Capability | None:
    with_pkg = [c for c in published if c.artifacts]
    if not with_pkg:
        return None
    return max(with_pkg, key=lambda c: parse_semver(c.version))


def package_base_for_save(
    draft: Capability | None, published: list[Capability]
) -> Capability | None:
    """保存时附属文件底稿：优先用当前草稿包，否则用最新已发布包。"""
    if draft is not None and draft.artifacts:
        return draft
    return latest_published_with_package(published)


def _is_blank_core(path: str, content: bytes, core_text_files: frozenset[str]) -> bool:
    matched = path in core_text_files
    if not matched and "implementation/*.py" in core_text_files:
        matched = path.startswith("implementation/") and path.endswith(".py")
    if not matched:
        return False
    try:
        text = content.decode("utf-8", errors="replace").strip()
    except Exception:  # noqa: BLE001
        return False
    if not text:
        return True
    # 空 JSON 对象/数组视为未填写，不覆盖已发布底稿
    if path.endswith(".json"):
        try:
            val = json.loads(text)
        except Exception:  # noqa: BLE001
            return False
        if val in ({}, [], None):
            return True
    return False


def merge_edit_package_files(
    cap: Capability,
    published: list[Capability],
    *,
    core_text_files: frozenset[str] = frozenset(),
) -> dict[str, bytes]:
    """编辑态文件：最新已发布包打底，当前草稿覆盖；草稿中空的核心文本不覆盖底稿。"""
    files: dict[str, bytes] = {}
    base = latest_published_with_package(published)
    if base is not None:
        files.update(read_capability_files(base))
    if cap.artifacts and (base is None or cap.id != base.id):
        for path, content in read_capability_files(cap).items():
            if _is_blank_core(path, content, core_text_files):
                continue
            files[path] = content
    elif not files:
        files.update(read_capability_files(cap))
    return files


def text_file(files: dict[str, bytes], path: str, default: str = "") -> str:
    raw = files.get(path)
    if raw is None:
        return default
    return raw.decode("utf-8", errors="replace")


def draft_policy_kwargs(base: Capability) -> dict:
    """新版本草稿从已发布行继承访问/安装策略与元数据标签。"""
    return {
        "access_policy": getattr(base, "access_policy", None) or "open",
        "allowed_users": list(getattr(base, "allowed_users", None) or []),
        "allowed_departments": list(getattr(base, "allowed_departments", None) or []),
        "allowed_roles": list(getattr(base, "allowed_roles", None) or []),
        "install_policy": getattr(base, "install_policy", None) or "optional",
        "distribution": getattr(base, "distribution", None) or "both",
        "risk_default": getattr(base, "risk_default", None) or "read",
        "data_domain": getattr(base, "data_domain", None) or "",
    }


def to_capability_out(
    cap: Capability,
    versions: list[Capability] | None = None,
    author_name: str | None = None,
) -> CapabilityOut:
    """安全序列化：仅读取已加载的关联，避免异步懒加载。"""
    data: dict[str, object] = {
        "id": cap.id,
        "name": cap.name,
        "description": cap.description or "",
        "type": cap.type,
        "version": cap.version,
        "status": cap.status,
        "category": cap.category or "",
        "tags": cap.tags or [],
        "visibility": cap.visibility,
        "access_policy": cap.access_policy or "open",
        "allowed_users": list(cap.allowed_users or []),
        "allowed_departments": list(cap.allowed_departments or []),
        "allowed_roles": list(cap.allowed_roles or []),
        "install_policy": getattr(cap, "install_policy", None) or "optional",
        "distribution": getattr(cap, "distribution", None) or "both",
        "risk_default": getattr(cap, "risk_default", None) or "read",
        "data_domain": getattr(cap, "data_domain", None) or "",
        "changelog": getattr(cap, "changelog", None) or "",
        "readme_md": getattr(cap, "readme_md", None) or "",
        "validation_report": getattr(cap, "validation_report", None) or {},
        "author_id": cap.author_id,
        "organization": cap.organization or "",
        "input_schema": cap.input_schema or {},
        "usage_count": cap.usage_count,
        "rating_sum": cap.rating_sum,
        "rating_count": cap.rating_count,
        "avg_rating": cap.avg_rating,
        "created_at": cap.created_at,
        "updated_at": cap.updated_at,
    }
    if author_name is not None:
        data["author_name"] = author_name
    elif "author" in cap.__dict__ and cap.author is not None:
        data["author_name"] = cap.author.username
    if "artifacts" in cap.__dict__:
        data["artifacts"] = [ArtifactOut.model_validate(a) for a in cap.artifacts]
    if versions is not None:
        data["latest"] = is_latest(cap, versions)
    from app.services.dashboard_consume import attach_consumer_fields

    attach_consumer_fields(data, cap)
    return CapabilityOut(**data)


async def get_visible_capabilities(
    db: AsyncSession, user: User | None, *, include_private: bool = False
) -> list[Capability]:
    stmt = select(Capability).options(
        selectinload(Capability.artifacts), joinedload(Capability.author)
    )
    result = await db.scalars(stmt)
    return [cap for cap in result if is_capability_visible(cap, user)]


def normalize_cap_name(name: str) -> str:
    """能力名规范化：去除首尾空白，空名 422。

    写入端必须统一调用：归属判定 / 平台密钥等按名资源解析都会 strip，
    带首尾空白的名称会造成同名绕过（跨作者 shadow / 认领他人密钥命名空间）。
    """
    normalized = (name or "").strip()
    if not normalized:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "能力名称不能为空")
    return normalized


async def ensure_name_ownership(db: AsyncSession, name: str, user: User) -> None:
    """名称归属：同名已存在且存在非本人作者的行 → 403；同作者多版本放行。

    admin 不豁免（代管走编辑既有行，见 publish _require_owner）。
    所有可能创建新 name 能力行的入口（市场创建、工作流、组装、plugin 组件等）都应调用。
    调用前须已经 ``normalize_cap_name``（本函数仅兜底 strip 查询）。
    """
    cap_name = (name or "").strip()
    if not cap_name:
        return
    foreign = await db.scalar(
        select(Capability.id)
        .where(and_(Capability.name == cap_name, Capability.author_id != user.id))
        .limit(1)
    )
    if foreign:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            f"能力名称 {cap_name} 已被其他作者占用，请换名或联系管理员",
        )


async def create_capability(
    db: AsyncSession, user: User, data: CapabilityCreate
) -> Capability:
    name = normalize_cap_name(data.name)
    exists = await db.scalar(
        select(Capability.id).where(
            and_(Capability.name == name, Capability.version == data.version)
        )
    )
    if exists:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"能力 {name} 已存在版本 {data.version}"
        )
    await ensure_name_ownership(db, name, user)
    cap = Capability(
        name=name,
        description=data.description,
        type=data.type,
        version=data.version,
        category=data.category,
        tags=data.tags,
        visibility=data.visibility,
        access_policy=data.access_policy,
        allowed_users=list(data.allowed_users or []),
        install_policy=data.install_policy or "optional",
        distribution=getattr(data, "distribution", None) or "both",
        risk_default=getattr(data, "risk_default", None) or "read",
        data_domain=(getattr(data, "data_domain", None) or "").strip(),
        author_id=user.id,
        organization=user.organization,
        status="draft",
    )
    db.add(cap)
    await db.commit()
    await db.refresh(cap)
    return cap


async def _sibling_count(db: AsyncSession, cap: Capability) -> int:
    return (
        await db.scalar(
            select(func.count())
            .select_from(Capability)
            .where(and_(Capability.name == cap.name, Capability.id != cap.id))
        )
    ) or 0


async def _clear_artifacts(db: AsyncSession, cap: Capability) -> None:
    storage = get_storage()
    rows = (
        await db.scalars(
            select(CapabilityArtifact).where(CapabilityArtifact.capability_id == cap.id)
        )
    ).all()
    for artifact in rows:
        try:
            storage.delete(artifact.uri)
        except Exception:
            pass
        await db.delete(artifact)


async def update_capability(
    db: AsyncSession, cap: Capability, data: CapabilityUpdate
) -> Capability:
    siblings = await _sibling_count(db, cap)
    new_name = normalize_cap_name(data.name) if data.name is not None else cap.name
    if new_name != cap.name:
        if siblings:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "该能力已有其他版本，不能改名"
            )
        # 名称归属：不能改成其他作者占用的名称（同作者已有版本可并入）
        foreign = await db.scalar(
            select(Capability.id)
            .where(
                and_(
                    Capability.name == new_name,
                    Capability.author_id != cap.author_id,
                    Capability.id != cap.id,
                )
            )
            .limit(1)
        )
        if foreign:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                f"能力名称 {new_name} 已被其他作者占用，不能改名",
            )
        taken = await db.scalar(
            select(Capability.id).where(
                and_(
                    Capability.name == new_name,
                    Capability.version == cap.version,
                    Capability.id != cap.id,
                )
            )
        )
        if taken:
            raise HTTPException(
                status.HTTP_409_CONFLICT, f"能力 {new_name} 已存在版本 {cap.version}"
            )
        old_name = cap.name
        cap.name = new_name
        # 改名要求无其他版本（siblings==0）：旧名最后一行释放，清理其平台密钥
        from app.services.capability_secrets import delete_capability_secrets_by_name

        await delete_capability_secrets_by_name(db, old_name)
    if data.type is not None and data.type != cap.type:
        if siblings:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "该能力已有其他版本，不能更改类型"
            )
        await _clear_artifacts(db, cap)
        cap.type = data.type
        cap.input_schema = {}
    if data.description is not None:
        cap.description = data.description
    if data.category is not None:
        cap.category = data.category
    if data.tags is not None:
        cap.tags = data.tags
    if data.visibility is not None:
        cap.visibility = data.visibility
    if data.access_policy is not None:
        cap.access_policy = data.access_policy
    if data.allowed_users is not None:
        cap.allowed_users = [u.strip() for u in data.allowed_users if u.strip()]
    if data.install_policy is not None:
        cap.install_policy = data.install_policy
    if data.distribution is not None:
        cap.distribution = data.distribution
    if data.risk_default is not None:
        cap.risk_default = data.risk_default
    if data.data_domain is not None:
        cap.data_domain = data.data_domain.strip()
    await db.commit()
    await db.refresh(cap)
    return cap


async def withdraw_capability(db: AsyncSession, cap: Capability, user: User) -> Capability:
    _transition(cap, "draft")
    db.add(
        Review(
            capability_id=cap.id,
            reviewer_id=user.id,
            action="withdrawn",
            comment="作者撤回审核",
        )
    )
    if cap.type == "plugin":
        from app.services.plugins import cascade_plugin_components

        await cascade_plugin_components(db, cap, action="withdraw")
    await db.commit()
    await db.refresh(cap)
    return cap


async def delete_capability_row(db: AsyncSession, cap: Capability, *, commit: bool = True) -> None:
    """删除单个能力及其关联行（可嵌套调用，由外层统一 commit）。"""
    storage = get_storage()
    rows = (
        await db.scalars(
            select(CapabilityArtifact).where(CapabilityArtifact.capability_id == cap.id)
        )
    ).all()
    for artifact in rows:
        try:
            storage.delete(artifact.uri)
        except Exception:
            pass
    others = (
        await db.scalar(
            select(func.count())
            .select_from(Capability)
            .where(and_(Capability.name == cap.name, Capability.id != cap.id))
        )
    ) or 0
    cap_name = cap.name
    cid = cap.id
    await db.execute(sql_delete(CapabilityArtifact).where(CapabilityArtifact.capability_id == cid))
    await db.execute(sql_delete(Review).where(Review.capability_id == cid))
    await db.execute(sql_delete(Rating).where(Rating.capability_id == cid))
    await db.execute(sql_delete(UsageEvent).where(UsageEvent.capability_id == cid))
    await db.execute(sql_delete(UserCapability).where(UserCapability.capability_id == cid))
    await db.execute(sql_delete(A2ATask).where(A2ATask.agent_id == cid))
    await db.execute(sql_delete(WorkflowExecution).where(WorkflowExecution.workflow_id == cid))
    await db.execute(sql_delete(AgentBinding).where(AgentBinding.agent_id == cid))
    await db.delete(cap)
    if others == 0:
        # 该 name 的最后一行被删除：清理平台密钥，避免名称释放后孤儿密钥被他人认领；
        # 仍有同名行时不清理（按名跨版本共用）
        from app.services.capability_secrets import delete_capability_secrets_by_name

        await delete_capability_secrets_by_name(db, cap_name)
    if commit:
        await db.commit()


async def delete_capability(db: AsyncSession, cap: Capability) -> None:
    if cap.type == "plugin":
        from app.services.plugins import cascade_plugin_components

        await cascade_plugin_components(db, cap, action="delete")
    await delete_capability_row(db, cap, commit=True)


async def submit_for_review(db: AsyncSession, cap: Capability) -> Capability:
    if cap.type in PACKAGE_REQUIRED_TYPES:
        has_artifact = await db.scalar(
            select(CapabilityArtifact.id)
            .where(CapabilityArtifact.capability_id == cap.id)
            .limit(1)
        )
        if has_artifact is None:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                "请先上传能力包后再提交审核",
            )
    _transition(cap, "reviewing")
    db.add(Review(capability_id=cap.id, reviewer_id=cap.author_id, action="submitted", comment="提交审核"))
    await db.commit()
    await db.refresh(cap)
    return cap


async def review_capability(
    db: AsyncSession,
    cap: Capability,
    reviewer: User,
    action: str,
    comment: str = "",
) -> Capability:
    if action == "take":
        if cap.status != "reviewing":
            raise HTTPException(status.HTTP_409_CONFLICT, "该能力不在待审状态")
        db.add(Review(capability_id=cap.id, reviewer_id=reviewer.id, action="reviewing", comment=comment or "开始审核"))
        await db.commit()
        return cap

    target = {"approve": "published", "reject": "rejected", "return": "returned"}.get(action)
    if target is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "未知审核动作")
    _transition(cap, target)
    db.add(Review(capability_id=cap.id, reviewer_id=reviewer.id, action=action, comment=comment))

    author = await db.get(User, cap.author_id)
    if author is not None:
        labels = {"approve": "已通过审核并发布", "reject": "被驳回", "return": "被打回修改"}
        db.add(
            Notification(
                user_id=author.id,
                title=f"能力 {cap.name} v{cap.version} {labels[action]}",
                body=comment or f"管理员已完成审核：{labels[action]}",
                link=f"/capabilities/{cap.id}",
            )
        )

    if target == "published":
        if cap.type == "plugin":
            from app.services.plugins import publish_plugin_components

            await publish_plugin_components(db, cap)
        await _deprecate_other_published(db, cap)
        await _notify_subscribers(db, cap)
    elif cap.type == "plugin" and target in ("rejected", "returned"):
        from app.services.plugins import cascade_plugin_components

        await cascade_plugin_components(db, cap, action="reject" if target == "rejected" else "return")
    await db.commit()
    await db.refresh(cap)
    return cap


async def _deprecate_other_published(db: AsyncSession, cap: Capability) -> None:
    """同一逻辑能力只保留一个正式版：新版本发布时，旧正式版自动转为「已弃用」。"""
    others = (
        await db.scalars(
            select(Capability).where(
                Capability.name == cap.name,
                Capability.type == cap.type,
                Capability.status == "published",
                Capability.id != cap.id,
            )
        )
    ).all()
    for old in others:
        old.status = "deprecated"
        db.add(
            Notification(
                user_id=old.author_id,
                title=f"能力 {old.name} v{old.version} 已被 v{cap.version} 替代",
                body=(
                    f"新正式版 v{cap.version} 已发布，旧版本自动转为「已弃用」。"
                    "如需继续使用请迁移到新版本。"
                ),
                link=f"/capabilities/{cap.id}",
            )
        )


async def _notify_subscribers(db: AsyncSession, cap: Capability) -> None:
    subs = (await db.scalars(select(Subscription).where(Subscription.capability_name == cap.name))).all()
    for sub in subs:
        if sub.user_id == cap.author_id:
            continue
        db.add(
            Notification(
                user_id=sub.user_id,
                title=f"你订阅的能力发布新版本：{cap.name} v{cap.version}",
                body=cap.description[:200] or f"{cap.name} v{cap.version} 已上架，点击查看。",
                link=f"/capabilities/{cap.id}",
            )
        )


async def change_status(db: AsyncSession, cap: Capability, target: str) -> Capability:
    _transition(cap, target)
    if target == "deprecated":
        db.add(
            Notification(
                user_id=cap.author_id,
                title=f"能力 {cap.name} v{cap.version} 已弃用",
                body="请迁移到新版本。",
                link=f"/capabilities/{cap.id}",
            )
        )
        if cap.type == "plugin":
            from app.services.plugins import cascade_plugin_components

            await cascade_plugin_components(db, cap, action="deprecate")
    await db.commit()
    await db.refresh(cap)
    return cap


def _transition(cap: Capability, target: str) -> None:
    if target not in STATUS_FLOW.get(cap.status, set()):
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            f"不允许从 {cap.status} 切换到 {target}",
        )
    cap.status = target


async def create_new_version(
    db: AsyncSession,
    user: User,
    cap: Capability,
    new_version: str,
    *,
    changelog: str = "",
) -> Capability:
    exists = await db.scalar(
        select(Capability.id).where(
            and_(Capability.name == cap.name, Capability.version == new_version)
        )
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"版本 {new_version} 已存在")
    new_cap = Capability(
        name=cap.name,
        description=cap.description,
        type=cap.type,
        version=new_version,
        changelog=changelog or "",
        category=cap.category,
        tags=list(cap.tags or []),
        visibility=cap.visibility,
        access_policy=cap.access_policy,
        allowed_users=list(cap.allowed_users or []),
        allowed_departments=list(cap.allowed_departments or []),
        allowed_roles=list(cap.allowed_roles or []),
        install_policy=getattr(cap, "install_policy", None) or "optional",
        distribution=getattr(cap, "distribution", None) or "both",
        risk_default=getattr(cap, "risk_default", None) or "read",
        data_domain=getattr(cap, "data_domain", None) or "",
        author_id=user.id,
        organization=cap.organization,
        status="draft",
        # 复制结构化元数据；artifact 需重新上传。plugin 的 components 仅作草案引用。
        input_schema=dict(cap.input_schema or {}),
    )
    db.add(new_cap)
    await db.commit()
    await db.refresh(new_cap)
    return new_cap


async def record_rating(db: AsyncSession, user: User, cap: Capability, score: int, comment: str) -> Rating:
    existing = await db.scalar(
        select(Rating).where(and_(Rating.capability_id == cap.id, Rating.user_id == user.id))
    )
    if existing:
        cap.rating_sum -= existing.score
        existing.score = score
        existing.comment = comment
        cap.rating_sum += score
        rating = existing
    else:
        rating = Rating(capability_id=cap.id, user_id=user.id, score=score, comment=comment)
        db.add(rating)
        cap.rating_count += 1
        cap.rating_sum += score
    await db.commit()
    await db.refresh(rating)
    return rating
