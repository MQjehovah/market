"""版本展示策略：列表只显示当前版本，草稿不进列表，编辑仍可操作草稿。"""

import pytest


async def _create_and_publish(client, pub, adm, name: str, version: str) -> str:
    r = await client.post(
        "/api/publish/capabilities",
        headers=pub,
        json={
            "name": name,
            "description": "版本测试",
            "type": "tool",
            "version": version,
            "category": "测试",
            "tags": [],
            "visibility": "internal",
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=pub)
    assert r.status_code == 200
    r = await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=adm,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    return cap_id


@pytest.mark.asyncio
async def test_browse_shows_only_latest_published(client, publisher_headers, admin_headers):
    name = "versioned-tool"
    await _create_and_publish(client, publisher_headers, admin_headers, name, "1.0.0")
    await _create_and_publish(client, publisher_headers, admin_headers, name, "1.0.1")

    # 再建一个未发布的草稿 v1.0.2
    r = await client.get("/api/capabilities", params={"q": name})
    cap_id = r.json()["items"][0]["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/versions",
        headers=publisher_headers,
        json={"new_version": "1.0.2", "change_type": "patch"},
    )
    assert r.status_code == 201, r.text

    # 默认列表：只显示最新已发布版本，草稿不显示
    r = await client.get("/api/capabilities", params={"q": name})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["version"] == "1.0.1"
    assert items[0]["status"] == "published"

    # 显式指定状态时仍可查到草稿（编辑/管理用）
    r = await client.get("/api/capabilities", params={"q": name, "status": "draft"})
    assert r.status_code == 200
    assert any(i["version"] == "1.0.2" for i in r.json()["items"])


@pytest.mark.asyncio
async def test_my_capabilities_grouped_with_draft_info(client, publisher_headers, admin_headers):
    name = "my-versioned-tool"
    await _create_and_publish(client, publisher_headers, admin_headers, name, "1.0.0")

    # 创建草稿 v1.0.1
    r = await client.get("/api/capabilities", params={"q": name})
    cap_id = r.json()["items"][0]["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/versions",
        headers=publisher_headers,
        json={"new_version": "1.0.1", "change_type": "patch"},
    )
    assert r.status_code == 201, r.text

    # 我的能力：同名只显示一行，展示当前版本 + 草稿信息
    r = await client.get("/api/my/capabilities", headers=publisher_headers)
    assert r.status_code == 200
    mine = [c for c in r.json() if c["name"] == name]
    assert len(mine) == 1
    assert mine[0]["version"] == "1.0.0"
    assert mine[0]["has_draft"] is True
    assert mine[0]["draft_version"] == "1.0.1"


@pytest.mark.asyncio
async def test_publish_new_version_deprecates_old(client, publisher_headers, admin_headers):
    """同一能力只保留一个正式版：新版本发布时，旧正式版自动转为已弃用。"""
    name = "single-published-tool"
    v100_id = await _create_and_publish(client, publisher_headers, admin_headers, name, "1.0.0")
    v101_id = await _create_and_publish(client, publisher_headers, admin_headers, name, "1.0.1")

    # 版本历史里旧版本已是已弃用、新版本是正式版
    r = await client.get(f"/api/capabilities/{v101_id}/versions")
    assert r.status_code == 200
    versions = {v["version"]: v["status"] for v in r.json()}
    assert versions["1.0.0"] == "deprecated"
    assert versions["1.0.1"] == "published"

    # 市场列表只显示新正式版
    r = await client.get("/api/capabilities", params={"q": name})
    assert r.status_code == 200
    items = r.json()["items"]
    assert len(items) == 1
    assert items[0]["id"] == v101_id
    assert items[0]["version"] == "1.0.1"

    # 再次发布 v1.0.2，v1.0.1 也转为已弃用
    await _create_and_publish(client, publisher_headers, admin_headers, name, "1.0.2")
    r = await client.get(f"/api/capabilities/{v100_id}/versions")
    versions = {v["version"]: v["status"] for v in r.json()}
    assert versions["1.0.0"] == "deprecated"
    assert versions["1.0.1"] == "deprecated"
    assert versions["1.0.2"] == "published"
