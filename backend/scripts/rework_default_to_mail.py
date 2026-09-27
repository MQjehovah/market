"""把 default（默认服务）改造为 mail（邮件）：改名 + 清掉 DB 查询残留 + 重建能力包。

在容器内运行： ``python scripts/rework_default_to_mail.py``
同步：订阅 / 平台密钥 / 插件组件引用 / input_schema 元数据。
（DEFAULT_CAPABILITIES 在宿主 .env 调整后需重建容器。）
"""

import asyncio
import copy
import io
import json
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.models import Capability, CapabilityArtifact, CapabilitySecret, Subscription
from app.storage import get_storage

OLD = "default"
NEW = "mail"
DISPLAY = "邮件"
DESC = "邮件发送服务：通过 SMTP 发送纯文本 / HTML 邮件，支持抄送。"
README = (
    "# mail（邮件发送服务）\n\n"
    "通过 SMTP 发送纯文本 / HTML 邮件，支持抄送。\n\n"
    "## 工具\n"
    "- `send_email`：发送邮件（to / subject / body / is_html / cc）\n"
    "- `get_current_time`：获取服务器当前时间\n\n"
    "环境变量：`SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM_NAME`。\n"
)


def _rework_package(blob: bytes) -> bytes:
    zf = zipfile.ZipFile(io.BytesIO(blob))
    files = {n: zf.read(n) for n in zf.namelist() if not n.endswith("/")}

    impl = files.get("implementation/default.py")
    if impl:
        code = impl.decode("utf-8", "replace")
        code = "\n".join(l for l in code.splitlines() if l.strip() != "import pymysql")
        files["implementation/default.py"] = code.encode("utf-8")

    try:
        meta = json.loads(files.get("mcp.json", b"{}").decode("utf-8"))
    except ValueError:
        meta = {}
    if isinstance(meta, dict):
        meta["name"] = NEW
        meta["description"] = DISPLAY
        files["mcp.json"] = json.dumps(meta, ensure_ascii=False, indent=2).encode("utf-8")

    files["README.md"] = README.encode("utf-8")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


async def main() -> None:
    async with SessionLocal() as db:
        rows = list(
            (
                await db.scalars(
                    select(Capability)
                    .options(selectinload(Capability.artifacts))
                    .where(Capability.name == OLD)
                )
            ).all()
        )
        if not rows:
            print("skip(未找到):", OLD)
            return
        clash = await db.scalar(select(Capability.id).where(Capability.name == NEW).limit(1))
        if clash:
            print("CLASH: 已存在", NEW)
            return

        storage = get_storage()
        for c in rows:
            c.name = NEW
            c.display_name = DISPLAY
            c.description = DESC
            c.readme_md = README
            schema = copy.deepcopy(c.input_schema or {})
            if isinstance(schema, dict) and schema.get("kind") == "mcp":
                schema["name"] = NEW
                schema["description"] = DISPLAY
                c.input_schema = schema
            if c.artifacts:
                blob = storage.open(c.artifacts[-1].uri).read()
                content = _rework_package(blob)
                filename = f"{NEW}-{c.version}.zip"
                info = storage.save(c.id, filename, io.BytesIO(content))
                db.add(CapabilityArtifact(capability_id=c.id, filename=filename, **info))
                print(f"repackaged {NEW} v{c.version}")

        # 订阅
        subs = list(
            (await db.scalars(select(Subscription).where(Subscription.capability_name == OLD))).all()
        )
        for s in subs:
            exists = await db.scalar(
                select(Subscription.id).where(
                    Subscription.user_id == s.user_id, Subscription.capability_name == NEW
                )
            )
            if exists:
                await db.delete(s)
            else:
                s.capability_name = NEW

        # 平台密钥
        secs = list(
            (
                await db.scalars(
                    select(CapabilitySecret).where(CapabilitySecret.capability_name == OLD)
                )
            ).all()
        )
        for s in secs:
            exists = await db.scalar(
                select(CapabilitySecret.id).where(
                    CapabilitySecret.capability_name == NEW, CapabilitySecret.key_name == s.key_name
                )
            )
            if exists:
                await db.delete(s)
            else:
                s.capability_name = NEW

        # 插件组件引用 default -> mail
        plugs = list(
            (await db.scalars(select(Capability).where(Capability.type == "plugin"))).all()
        )
        for p in plugs:
            ps = copy.deepcopy(p.input_schema or {})
            changed = False
            for comp in ps.get("components") or []:
                if comp.get("type") == "mcp" and comp.get("name") == OLD:
                    comp["name"] = NEW
                    changed = True
            if changed:
                p.input_schema = ps
                print("plugin component updated:", p.name)

        await db.commit()

    async with SessionLocal() as db:
        c = await db.scalar(select(Capability).where(Capability.name == NEW).limit(1))
        print("check:", NEW, "->", (c.status if c else "MISSING"), "v" + (c.version if c else ""),
              "display=", (c.display_name if c else None))


if __name__ == "__main__":
    asyncio.run(main())
