"""DWS 内置能力与 runtime 路由。"""

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Capability


async def _dws_cap(db):
    return await db.scalar(
        select(Capability)
        .where(Capability.name == "dingtalk-dws", Capability.status == "published")
        .order_by(Capability.created_at.desc())
    )


async def test_builtin_dws_capability_is_mcp(client):
    async with SessionLocal() as db:
        cap = await _dws_cap(db)
        assert cap is not None
        assert cap.type == "mcp"
        assert cap.binding == "user_only"
        assert cap.input_schema["required_env"] == [
            "DWS_CONFIG_DIR",
            "DWS_KEYCHAIN_DIR",
            "DWS_WORKDIR",
        ]
        assert cap.config_ui["provider"] == "dws"


async def test_dws_mcp_is_visible_and_joinable(client, user_headers):
    async with SessionLocal() as db:
        cap = await _dws_cap(db)
        cap_id = cap.id

    joined = await client.post(
        "/api/my/capabilities", json={"capability_id": cap_id}, headers=user_headers
    )
    assert joined.status_code in (200, 201), joined.text
