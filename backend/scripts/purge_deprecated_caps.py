"""清理已弃用且服务端失效的旧能力：remote_operation / ticket_ops（所有版本）。

在容器内运行： ``python scripts/purge_deprecated_caps.py``
它们已被 rosiwit-cloud-remote-api / rosiwit-cloud-ticket 取代，且旧域名/凭据调用 403，
继续留在目录会被模型误选。
"""

import asyncio

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Capability
from app.services.capabilities import delete_capability

NAMES = ("remote_operation", "ticket_ops")


async def main() -> None:
    async with SessionLocal() as db:
        for name in NAMES:
            rows = list(
                (await db.scalars(select(Capability).where(Capability.name == name))).all()
            )
            if not rows:
                print("skip(未找到):", name)
                continue
            for cap in rows:
                version, status = cap.version, cap.status
                await delete_capability(db, cap)
                print(f"deleted {name} v{version} [{status}]")

    async with SessionLocal() as db:
        for name in NAMES:
            left = (await db.scalars(select(Capability.id).where(Capability.name == name))).all()
            print(f"verify {name}: remaining={len(left)}")


if __name__ == "__main__":
    asyncio.run(main())
