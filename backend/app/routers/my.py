"""我的能力：从市场加入的能力集合 + 我创建的能力，支持加入 / 移除 / 按来源筛选。"""

import logging
from typing import Annotated
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import and_, select
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, DbSession
from app.models import Capability, Subscription, User, UserCapability
from app.schemas import (
    HostSyncOut,
    MessageOut,
    MyCapabilityAdd,
    MyCapabilityPatch,
    MySubscriptionsOut,
    UserSecretBulkUpsert,
    UserSecretOut,
    UserSecretStatusOut,
    UserSecretUpsert,
)
from app.services.access import access_deny_reason, accessible_connected_ids, capability_access_ok
from app.services.act_as import resolve_act_as
from app.services.capabilities import parse_semver, to_capability_out
from app.services.dashboard_consume import attach_consumer_fields
from app.services.install_policy import ensure_default_on_joins, is_required_policy
from app.services.secret_vault import (
    delete_secret,
    list_secrets,
    secret_status,
    to_secret_out,
    upsert_secret,
    upsert_secrets_bulk,
)
from app.services.service_tokens import is_service_token
from app.services.taxonomy import KIND_META
from app.services.visibility import is_capability_visible

router = APIRouter(prefix="/api/my", tags=["my"])
logger = logging.getLogger("market.my")

# 宿主可落地的 kind（与 taxonomy local_install + 远程 tool 对齐；不含 workflow）
_HOST_KINDS = {
    k
    for k, meta in KIND_META.items()
    if meta.get("local_install") == "yes" or k == "tool"
}


def _out(cap: Capability, *, added: bool, owned: bool, enabled: bool = True) -> dict:
    data = to_capability_out(
        cap, author_name=cap.author.username if cap.author else ""
    ).model_dump()
    data["added"] = added
    data["owned"] = owned
    data["enabled"] = bool(enabled)
    data["has_artifact"] = bool(getattr(cap, "artifacts", None))
    data["removable"] = not is_required_policy(cap)
    return data


async def current_my_user(request: Request, db: DbSession, user: CurrentUser) -> User:
    """`/my` 操作主体：服务令牌 + X-Act-As-Sub 时代表目标用户执行（agent web 代理）。

    仅服务令牌生效（scope 由 resolve_act_as 兜底校验 gateway/sync 任一）；
    非服务令牌带该头忽略（沿用全局约定）；无该头时保持服务自身/本人身份。
    审计口径与 sync/relay 一致：act_as 落到目标用户名，actor 为服务令牌绑定用户。
    """
    sub = (request.headers.get("x-act-as-sub") or "").strip()
    if not is_service_token(user):
        if sub:
            logger.debug("非服务令牌携带 X-Act-As-Sub，已忽略 path=%s", request.url.path)
        return user
    if not sub:
        return user
    target = await resolve_act_as(db, user, sub)
    if target is not None:
        logger.info(
            "我的能力代表用户访问：actor=%s act_as=%s path=%s",
            user.id,
            target.username,
            request.url.path,
        )
        return target
    return user


MyUser = Annotated[User, Depends(current_my_user)]


@router.get("/capabilities")
async def my_capabilities(
    db: DbSession, user: MyUser, scope: str = "all", include_components: bool = False
):
    """我的能力列表：scope=all | added（从市场加入）| owned（我创建的）。

    每个逻辑能力只展示一个“当前版本”（最新版本）；若存在草稿/被打回版本，
    附带 draft_* 信息供编辑入口使用（编辑仍可操作草稿）。

    include_components：默认 false，隐藏 plugin 拆出的子能力（仍可在 plugin 详情查看）。
    访问列表时会同步加入 install_policy=default_on 的已发布能力。
    """
    # 默认能力仅开户时开通一次（见 auth），列表不再自动补加，确保「移出」生效
    rows = (
        await db.scalars(
            select(UserCapability)
            .options(
                joinedload(UserCapability.capability).joinedload(Capability.author),
                joinedload(UserCapability.capability).selectinload(Capability.artifacts),
            )
            .where(UserCapability.user_id == user.id)
        )
    ).all()
    enabled_by_id = {row.capability_id: bool(getattr(row, "enabled", True)) for row in rows}
    owned = (
        await db.scalars(
            select(Capability)
            .options(
                joinedload(Capability.author),
                selectinload(Capability.artifacts),
            )
            .where(Capability.author_id == user.id)
        )
    ).all()

    items: dict[str, dict] = {}
    # 第一优先：最新已发布版本（只显示当前版本）
    published_by_name: dict[str, Capability] = {}
    for cap in owned:
        if cap.status == "published":
            cur = published_by_name.get(cap.name)
            if cur is None or parse_semver(cap.version) > parse_semver(cur.version):
                published_by_name[cap.name] = cap
    for key, cap in published_by_name.items():
        items[key] = _out(cap, added=False, owned=True)
    # 无已发布版本的能力（纯草稿/被打回），取最新一版展示，便于提交审核
    for cap in owned:
        if cap.status != "published" and cap.name not in items:
            items[cap.name] = _out(cap, added=False, owned=True)

    for row in rows:
        cap = row.capability
        if cap is None:
            continue
        key = cap.name
        if key in items:
            if parse_semver(cap.version) > parse_semver(items[key]["version"]):
                owned_flag = items[key]["owned"]
                items[key] = _out(
                    cap,
                    added=True,
                    owned=owned_flag,
                    enabled=enabled_by_id.get(cap.id, True),
                )
            else:
                items[key]["added"] = True
                items[key]["enabled"] = enabled_by_id.get(items[key]["id"], True)
        else:
            items[key] = _out(
                cap, added=True, owned=False, enabled=enabled_by_id.get(cap.id, True)
            )

    # 附带同名下最新草稿/被打回版本信息（用于「编辑草稿 / 提交审核」）
    drafts_by_name: dict[str, Capability] = {}
    for cap in owned:
        if cap.status in ("draft", "returned", "rejected"):
            cur = drafts_by_name.get(cap.name)
            if cur is None or parse_semver(cap.version) > parse_semver(cur.version):
                drafts_by_name[cap.name] = cap
    for key, item in items.items():
        draft = drafts_by_name.get(key)
        if draft is not None and draft.id != item["id"]:
            item["has_draft"] = True
            item["draft_id"] = draft.id
            item["draft_version"] = draft.version
            item["draft_status"] = draft.status
            item["draft_has_artifact"] = bool(getattr(draft, "artifacts", None))
        else:
            item["has_draft"] = False
            item["draft_has_artifact"] = False

    result = list(items.values())
    if not include_components:
        result = [
            i
            for i in result
            if "plugin-component" not in (i.get("tags") or [])
        ]
    if scope == "added":
        result = [i for i in result if i["added"]]
    elif scope == "owned":
        result = [i for i in result if i["owned"]]

    def _key(item: dict):
        try:
            return (item["name"], parse_semver(item["version"]))
        except ValueError:
            return (item["name"], (0, 0, 0))

    result.sort(key=_key, reverse=True)
    return result


@router.get("/host-sync", response_model=HostSyncOut)
async def host_sync(db: DbSession, user: CurrentUser, include_components: bool = False):
    """宿主同步清单：已加入且启用、可落地的已发布能力。

    legacy：安装与启用改为各运行端本地各记，市场不再提供 UI 引用；
    保留本接口与 PATCH /my/capabilities 供未来宿主清单兼容，不改字段。
    """
    # 默认能力仅开户时开通一次（见 auth），列表不再自动补加，确保「移出」生效
    rows = (
        await db.scalars(
            select(UserCapability)
            .options(
                joinedload(UserCapability.capability).joinedload(Capability.author),
                joinedload(UserCapability.capability).selectinload(Capability.artifacts),
            )
            .where(UserCapability.user_id == user.id)
        )
    ).all()

    items: list[dict] = []
    for row in rows:
        if getattr(row, "enabled", True) is False:
            continue
        cap = row.capability
        if cap is None:
            continue
        if cap.status not in ("published", "deprecated"):
            continue
        if cap.type not in _HOST_KINDS:
            continue
        tags = cap.tags or []
        if not include_components and "plugin-component" in tags:
            continue
        if not cap.artifacts and cap.type != "tool":
            # tool 走云端 invoke，无 zip 也可进清单
            continue
        data = to_capability_out(
            cap, author_name=cap.author.username if cap.author else ""
        ).model_dump()
        attach_consumer_fields(data, cap)
        download_url = ""
        if cap.artifacts:
            download_url = (
                f"/api/capabilities/{quote(cap.name, safe='')}/download"
                f"?version={quote(cap.version, safe='')}&type={quote(cap.type, safe='')}"
            )
        items.append(
            {
                "id": cap.id,
                "name": cap.name,
                "type": cap.type,
                "version": cap.version,
                "description": cap.description or "",
                "enabled": True,
                "download_url": download_url,
                "consumers": data.get("consumers") or {},
            }
        )

    items.sort(key=lambda i: (i["type"], i["name"]))
    return HostSyncOut(items=items)


@router.post("/capabilities", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def add_capability(data: MyCapabilityAdd, db: DbSession, user: MyUser):
    cap = await db.scalar(
        select(Capability)
        .options(joinedload(Capability.author), selectinload(Capability.artifacts))
        .where(Capability.id == data.capability_id)
    )
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    if not is_capability_visible(cap, user):
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    if cap.status not in ("published", "deprecated"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "只有已发布（或弃用期内）的能力可以加入我的能力",
        )
    if not capability_access_ok(cap, user):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, f"没有加入权限（{access_deny_reason(cap, user)}）"
        )

    # 连带能力（plugin 组件 / agent 依赖）逐个过滤：不可见或无访问权限则跳过
    ids, skipped = await accessible_connected_ids(db, user, cap)

    added = 0
    for cid in ids:
        exists = await db.scalar(
            select(UserCapability.id).where(
                and_(
                    UserCapability.user_id == user.id,
                    UserCapability.capability_id == cid,
                )
            )
        )
        if exists:
            continue
        db.add(UserCapability(user_id=user.id, capability_id=cid))
        added += 1
    await db.commit()
    skipped_unit = "依赖" if cap.type == "agent" else "组件"
    skipped_note = f"（{skipped} 个{skipped_unit}因权限不足未加入）" if skipped else ""
    if added == 0:
        return MessageOut(message=f"已在你的能力中{skipped_note}")
    dep_count = len(ids) - 1
    if cap.type == "plugin" and dep_count > 0:
        return MessageOut(message=f"已加入能力包及其 {dep_count} 个组件{skipped_note}")
    if cap.type == "agent" and dep_count > 0:
        return MessageOut(message=f"已加入专家及其 {dep_count} 个依赖{skipped_note}")
    return MessageOut(message=f"已加入我的能力{skipped_note}")


@router.patch("/capabilities/{capability_id}", response_model=MessageOut)
async def patch_capability(
    capability_id: str, data: MyCapabilityPatch, db: DbSession, user: CurrentUser
):
    """legacy：宿主启用状态（本地各记后市场不再提供 UI 开关），保留供宿主兼容。"""
    row = await db.scalar(
        select(UserCapability).where(
            and_(
                UserCapability.user_id == user.id,
                UserCapability.capability_id == capability_id,
            )
        )
    )
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该能力不在你的能力中")
    cap = await db.get(Capability, capability_id)
    if data.enabled is False and is_required_policy(cap):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"「{cap.name}」为必装能力（install_policy=required），不能停用",
        )
    ids = [capability_id]
    if cap is not None and cap.type == "plugin":
        from app.services.plugins import plugin_component_ids

        ids.extend(plugin_component_ids(cap))
    updated = 0
    for cid in ids:
        link = await db.scalar(
            select(UserCapability).where(
                and_(
                    UserCapability.user_id == user.id,
                    UserCapability.capability_id == cid,
                )
            )
        )
        if link is None:
            continue
        child = await db.get(Capability, cid)
        if data.enabled is False and is_required_policy(child):
            continue
        link.enabled = data.enabled
        updated += 1
    await db.commit()
    state = "启用" if data.enabled else "停用"
    return MessageOut(message=f"已{state}（{updated} 项）")


@router.delete("/capabilities/{capability_id}", response_model=MessageOut)
async def remove_capability(capability_id: str, db: DbSession, user: MyUser):
    row = await db.scalar(
        select(UserCapability).where(
            and_(
                UserCapability.user_id == user.id,
                UserCapability.capability_id == capability_id,
            )
        )
    )
    if row is None:
        return MessageOut(message="该能力不在你的能力中")

    cap = await db.get(Capability, capability_id)
    if is_required_policy(cap):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"「{cap.name}」为必装能力（install_policy=required），不能从我的能力中移除",
        )
    ids = [capability_id]
    if cap is not None and cap.type == "plugin":
        from app.services.plugins import plugin_component_ids

        ids.extend(plugin_component_ids(cap))

    removed = 0
    for cid in ids:
        link = await db.scalar(
            select(UserCapability).where(
                and_(
                    UserCapability.user_id == user.id,
                    UserCapability.capability_id == cid,
                )
            )
        )
        if link is None:
            continue
        await db.delete(link)
        removed += 1
    await db.commit()
    if cap is not None and cap.type == "plugin" and removed > 1:
        return MessageOut(message=f"已从我的能力移除能力包及其 {removed - 1} 个组件")
    return MessageOut(message="已从我的能力移除")


@router.get("/subscriptions", response_model=MySubscriptionsOut)
async def my_subscriptions(db: DbSession, user: MyUser):
    """当前用户的订阅更新清单（能力名），供详情页「订阅更新/取消订阅更新」两态。"""
    names = (
        await db.scalars(
            select(Subscription.capability_name)
            .where(Subscription.user_id == user.id)
            .order_by(Subscription.capability_name)
        )
    ).all()
    return MySubscriptionsOut(names=list(names))


# ── 业务密钥托管 ──────────────────────────────────────────────


@router.get("/secrets", response_model=list[UserSecretOut])
async def my_secrets(db: DbSession, user: CurrentUser, scope: str | None = None):
    """列出我的托管密钥元数据（从不返回明文）。scope 不传=全部；传空串=仅全局。"""
    rows = await list_secrets(db, user.id, scope=scope)
    return [UserSecretOut(**to_secret_out(r)) for r in rows]


@router.put("/secrets", response_model=UserSecretOut)
async def put_secret(data: UserSecretUpsert, db: DbSession, user: CurrentUser):
    """写入/更新一条托管密钥（明文仅本次请求携带，落库为 Fernet 密文）。"""
    row = await upsert_secret(
        db,
        user.id,
        key_name=data.key_name,
        value=data.value,
        scope=data.scope,
        label=data.label,
    )
    await db.commit()
    await db.refresh(row)
    return UserSecretOut(**to_secret_out(row))


@router.put("/secrets/bulk", response_model=list[UserSecretOut])
async def put_secrets_bulk(data: UserSecretBulkUpsert, db: DbSession, user: CurrentUser):
    """批量写入（常用于能力详情页一次保存 required_env）。空值跳过。"""
    rows = await upsert_secrets_bulk(
        db, user.id, secrets=data.secrets, scope=data.scope
    )
    await db.commit()
    for row in rows:
        await db.refresh(row)
    return [UserSecretOut(**to_secret_out(r)) for r in rows]


@router.get("/secrets/status", response_model=UserSecretStatusOut)
async def secrets_status(
    db: DbSession,
    user: CurrentUser,
    keys: str = "",
    capability_id: str = "",
):
    """检查 required keys 是否已在托管中填齐。keys 为逗号分隔。"""
    required = [k.strip() for k in keys.split(",") if k.strip()]
    status = await secret_status(
        db, user.id, required_keys=required, capability_id=capability_id or None
    )
    return UserSecretStatusOut(**status)


@router.delete("/secrets/{secret_id}", response_model=MessageOut)
async def remove_secret(secret_id: str, db: DbSession, user: CurrentUser):
    await delete_secret(db, user.id, secret_id)
    await db.commit()
    return MessageOut(message="已删除托管密钥")
