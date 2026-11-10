"""平台能力说明文档：启动时写入 readme_md，供市场与 Agent 展示。"""

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Capability, User
from app.services.capability_docs import DOCS, ensure_capability_docs


def test_docs_cover_builtin_capabilities():
    assert {"dingtalk-dws", "dingtalk-openapi", "erp"} <= set(DOCS)
    assert all(len(doc.strip()) > 100 for doc in DOCS.values())


async def test_ensure_capability_docs_writes_by_name(client):
    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.role == "admin"))
        assert admin is not None
        for name in DOCS:
            db.add(
                Capability(
                    name=name,
                    type="mcp",
                    version="0.0.1-test",
                    status="published",
                    author_id=admin.id,
                    readme_md="旧说明",
                )
            )
        await db.commit()

        await ensure_capability_docs(db)
        await db.commit()

        for name, doc in DOCS.items():
            row = await db.scalar(
                select(Capability).where(
                    Capability.name == name,
                    Capability.version == "0.0.1-test",
                )
            )
            assert row is not None
            assert row.readme_md == doc
