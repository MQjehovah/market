"""平台能力改名（一次性）：设备云三件套统一 rosiwit-cloud-* 命名 + 工单系统显示名。

在容器内运行： ``python scripts/rename_capabilities.py``
同时同步：订阅(subscriptions)、平台密钥(capability_secrets)、插件组件引用。
（DEFAULT_CAPABILITIES 在宿主 .env 调整后需重建容器。）
"""

import asyncio
import copy

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Capability, CapabilitySecret, Subscription

# 旧名 -> (新名, 新显示名)
RENAME: dict[str, tuple[str, str]] = {
    "remote_terminal": ("rosiwit-cloud-remote-terminal", "远程终端"),
    "rosiwit-cloud-remote": ("rosiwit-cloud-remote-api", "设备远程接口"),
    "rosiwit-cloud-ticket": ("rosiwit-cloud-ticket", "工单系统"),
}

# 插件组件名重定向（旧 -> 新）
COMP_ALIAS = {
    "remote_terminal": "rosiwit-cloud-remote-terminal",
    "rosiwit-cloud-remote": "rosiwit-cloud-remote-api",
}


async def main() -> None:
    async with SessionLocal() as db:
        for old, (new, disp) in RENAME.items():
            rows = list(
                (await db.scalars(select(Capability).where(Capability.name == old))).all()
            )
            if not rows:
                print("skip(未找到):", old)
                continue
            if new != old:
                clash = await db.scalar(
                    select(Capability.id).where(Capability.name == new).limit(1)
                )
                if clash:
                    print("CLASH(新名已存在，跳过):", new)
                    continue
            for c in rows:
                c.name = new
                if disp:
                    c.display_name = disp

            subs = list(
                (
                    await db.scalars(
                        select(Subscription).where(Subscription.capability_name == old)
                    )
                ).all()
            )
            for s in subs:
                exists = await db.scalar(
                    select(Subscription.id).where(
                        Subscription.user_id == s.user_id,
                        Subscription.capability_name == new,
                    )
                )
                if exists:
                    await db.delete(s)
                else:
                    s.capability_name = new

            secs = list(
                (
                    await db.scalars(
                        select(CapabilitySecret).where(CapabilitySecret.capability_name == old)
                    )
                ).all()
            )
            for s in secs:
                exists = await db.scalar(
                    select(CapabilitySecret.id).where(
                        CapabilitySecret.capability_name == new,
                        CapabilitySecret.key_name == s.key_name,
                    )
                )
                if exists:
                    await db.delete(s)
                else:
                    s.capability_name = new

            print(f"renamed {old} -> {new} (display={disp!r}) rows={len(rows)} subs={len(subs)} secrets={len(secs)}")

        # 插件组件引用改名
        plugs = list(
            (await db.scalars(select(Capability).where(Capability.type == "plugin"))).all()
        )
        for p in plugs:
            schema = copy.deepcopy(p.input_schema or {})
            changed = False
            for comp in schema.get("components") or []:
                name = comp.get("name")
                if name in COMP_ALIAS:
                    comp["name"] = COMP_ALIAS[name]
                    changed = True
            if changed:
                p.input_schema = schema
                print("plugin components updated:", p.name)

        await db.commit()

    # 复核
    async with SessionLocal() as db:
        caps = list((await db.scalars(select(Capability))).all())
        by = {c.name: c for c in caps}
        for _, (new, _disp) in RENAME.items():
            c = by.get(new)
            print("  check", new, "->", (c.status if c else "MISSING"),
                  "display=", (c.display_name if c else None))


if __name__ == "__main__":
    asyncio.run(main())
