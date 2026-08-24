"""我的能力：从市场加入的能力集合 + 我创建的能力，支持加入 / 移除 / 按来源筛选。"""

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.orm import joinedload

from app.auth import CurrentUser, DbSession
from app.models import Capability, UserCapability
from app.schemas import MessageOut, MyCapabilityAdd
from app.services.capabilities import parse_semver, to_capability_out

router = APIRouter(prefix="/api/my", tags=["my"])


def _out(cap: Capability, *, added: bool, owned: bool) -> dict:
    data = to_capability_out(
        cap, author_name=cap.author.username if cap.author else ""
    ).model_dump()
    data["added"] = added
    data["owned"] = owned
    return data


@router.get("/capabilities")
async def my_capabilities(
    db: DbSession, user: CurrentUser, scope: str = "all", include_components: bool = False
):
    """我的能力列表：scope=all | added（从市场加入）| owned（我创建的）。

    每个逻辑能力只展示一个“当前版本”（最新版本）；若存在草稿/被打回版本，
    附带 draft_* 信息供编辑入口使用（编辑仍可操作草稿）。

    include_components：默认 false，隐藏 plugin 拆出的子能力（仍可在 plugin 详情查看）。
    """
    rows = (
        await db.scalars(
            select(UserCapability)
            .options(joinedload(UserCapability.capability).joinedload(Capability.author))
            .where(UserCapability.user_id == user.id)
        )
    ).all()
    owned = (
        await db.scalars(
            select(Capability)
            .options(joinedload(Capability.author))
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
                items[key] = _out(cap, added=True, owned=items[key]["owned"])
            else:
                items[key]["added"] = True
        else:
            items[key] = _out(cap, added=True, owned=False)

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
        else:
            item["has_draft"] = False

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


@router.post("/capabilities", response_model=MessageOut, status_code=status.HTTP_201_CREATED)
async def add_capability(data: MyCapabilityAdd, db: DbSession, user: CurrentUser):
    cap = await db.get(Capability, data.capability_id)
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    if cap.status not in ("published", "deprecated"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "只有已发布（或弃用期内）的能力可以加入我的能力",
        )

    ids = [cap.id]
    if cap.type == "plugin":
        from app.services.plugins import plugin_component_ids

        ids.extend(plugin_component_ids(cap))

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
    if added == 0:
        return MessageOut(message="已在你的能力中")
    if cap.type == "plugin" and len(ids) > 1:
        return MessageOut(message=f"已加入插件及其 {len(ids) - 1} 个组件")
    return MessageOut(message="已加入我的能力")


@router.delete("/capabilities/{capability_id}", response_model=MessageOut)
async def remove_capability(capability_id: str, db: DbSession, user: CurrentUser):
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
        return MessageOut(message=f"已从我的能力移除插件及其 {removed - 1} 个组件")
    return MessageOut(message="已从我的能力移除")
