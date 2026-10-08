"""部门名录：登记、改名同步成员与白名单、空部门可删。"""

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.models import Capability, User


@pytest.mark.asyncio
async def test_departments_require_admin(client, user_headers):
    r = await client.get("/api/admin/departments", headers=user_headers)
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_department_catalog_rename_and_delete(client, admin_headers):
    r = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "username": "deptmember",
            "email": "deptmember@example.com",
            "password": "secret123",
            "department": "研发部",
        },
    )
    assert r.status_code == 201, r.text
    uid = r.json()["id"]

    r = await client.get("/api/admin/departments", headers=admin_headers)
    assert r.status_code == 200, r.text
    rows = r.json()
    research = next(d for d in rows if d["name"] == "研发部")
    assert research["member_count"] == 1
    assert research["id"]

    async with SessionLocal() as db:
        admin = await db.scalar(select(User).where(User.username == "admin"))
        db.add(
            Capability(
                name="dept-gate",
                type="tool",
                version="1.0.0",
                author_id=admin.id,
                organization="研发部",
                allowed_departments=["研发部", "市场部"],
            )
        )
        await db.commit()

    r = await client.post(
        "/api/admin/departments",
        headers=admin_headers,
        json={"name": " 空部门 "},
    )
    assert r.status_code == 201, r.text
    assert r.json()["name"] == "空部门"
    empty_id = r.json()["id"]

    r = await client.post(
        "/api/admin/departments", headers=admin_headers, json={"name": "空部门"}
    )
    assert r.status_code == 409

    r = await client.patch(
        f"/api/admin/departments/{research['id']}",
        headers=admin_headers,
        json={"name": "研发中心"},
    )
    assert r.status_code == 200, r.text
    assert "研发中心" in r.json()["message"]

    r = await client.get("/api/admin/users", headers=admin_headers)
    member = next(u for u in r.json() if u["id"] == uid)
    assert member["department"] == "研发中心"

    async with SessionLocal() as db:
        cap = await db.scalar(select(Capability).where(Capability.name == "dept-gate"))
        assert cap.organization == "研发中心"
        assert cap.allowed_departments == ["研发中心", "市场部"]
        linked = await db.get(User, uid)
        assert linked.department_id == research["id"]

    r = await client.post(
        "/api/admin/departments", headers=admin_headers, json={"name": "平台研发"}
    )
    target_id = r.json()["id"]
    r = await client.patch(
        f"/api/admin/departments/{research['id']}",
        headers=admin_headers,
        json={"name": "平台研发"},
    )
    assert r.status_code == 200, r.text
    assert "并入" in r.json()["message"]

    r = await client.get("/api/admin/departments", headers=admin_headers)
    names = {d["name"]: d for d in r.json()}
    assert "研发中心" not in names
    assert names["平台研发"]["id"] == target_id
    assert names["平台研发"]["member_count"] == 1

    r = await client.delete(f"/api/admin/departments/{target_id}", headers=admin_headers)
    assert r.status_code == 409

    r = await client.delete(f"/api/admin/departments/{empty_id}", headers=admin_headers)
    assert r.status_code == 200, r.text
    r = await client.get("/api/admin/departments", headers=admin_headers)
    assert all(d["name"] != "空部门" for d in r.json())
