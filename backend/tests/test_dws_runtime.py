"""DWS 内置能力与 runtime 路由。"""

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Capability, DwsAccount, User


async def _dws_cap(db):
    return await db.scalar(
        select(Capability)
        .where(Capability.name == "dingtalk-dws", Capability.status == "published")
        .order_by(Capability.created_at.desc())
    )


async def _user(db, username="user"):
    return await db.scalar(select(User).where(User.username == username))


async def test_builtin_dws_capability_exists(client):
    async with SessionLocal() as db:
        cap = await _dws_cap(db)
        assert cap is not None
        assert cap.binding == "user_only"
        assert cap.input_schema["required"] == ["command", "mode"]


async def test_runtime_routes_dws_to_executor(client, user_headers, monkeypatch):
    from app.dws import executor as dws_executor

    async with SessionLocal() as db:
        cap = await _dws_cap(db)
        user = await _user(db)
        db.add(DwsAccount(user_id=user.id, status="authorized"))
        await db.commit()
        cap_id = cap.id

    joined = await client.post("/api/my/capabilities", json={"capability_id": cap_id}, headers=user_headers)
    assert joined.status_code in (200, 201), joined.text

    async def fake_execute(db, user, cap, params):
        assert params["command"] == "calendar event list"
        return {"success": True, "command": params["command"], "effect": "read", "data": []}

    monkeypatch.setattr(dws_executor, "execute_dws", fake_execute)
    r = await client.post(
        "/api/runtime/tools/dingtalk-dws/invoke",
        json={"params": {"command": "calendar event list", "mode": "read"}},
        headers=user_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"]["success"] is True
    assert body["result"]["output"]["data"] == []
