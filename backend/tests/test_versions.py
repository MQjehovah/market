"""版本展示策略：列表只显示当前版本，草稿不进列表，编辑仍可操作草稿。"""

import io
import json
import zipfile

import pytest


def _tool_zip(name: str, version: str = "1.0.0") -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "tool.json",
            json.dumps({"name": name, "description": "版本测试", "version": version}, ensure_ascii=False),
        )
        zf.writestr(
            "schema.json",
            json.dumps({"type": "object", "properties": {"x": {"type": "string"}}}),
        )
        zf.writestr("implementation/tool.py", "def run(params):\n    return {'ok': True}\n")
    return buf.getvalue()


async def _upload_tool_package(client, headers, cap_id: str, name: str, version: str) -> None:
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=headers,
        files={"file": ("pkg.zip", _tool_zip(name, version), "application/zip")},
    )
    assert r.status_code == 200, r.text


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
    await _upload_tool_package(client, pub, cap_id, name, version)
    r = await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=pub)
    assert r.status_code == 200, r.text
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


@pytest.mark.asyncio
async def test_submit_requires_package(client, publisher_headers):
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": "need-pkg-tool",
            "description": "无包不可提交",
            "type": "tool",
            "version": "0.1.0",
            "visibility": "internal",
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    assert r.status_code == 409
    assert "能力包" in r.json()["detail"] or "上传" in r.json()["detail"]


@pytest.mark.asyncio
async def test_upload_rejected_on_published(client, publisher_headers, admin_headers):
    cap_id = await _create_and_publish(
        client, publisher_headers, admin_headers, "no-reupload-tool", "1.0.0"
    )
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", _tool_zip("no-reupload-tool", "1.0.0"), "application/zip")},
    )
    assert r.status_code == 409


@pytest.mark.asyncio
async def test_publish_my_latest_is_per_name(client, publisher_headers, admin_headers):
    await _create_and_publish(client, publisher_headers, admin_headers, "alpha-tool", "2.0.0")
    await _create_and_publish(client, publisher_headers, admin_headers, "beta-tool", "1.0.0")

    r = await client.get("/api/publish/my", headers=publisher_headers)
    assert r.status_code == 200
    by_name = {c["name"]: c for c in r.json() if c["name"] in ("alpha-tool", "beta-tool")}
    assert by_name["alpha-tool"]["latest"] is True
    assert by_name["beta-tool"]["latest"] is True


@pytest.mark.asyncio
async def test_create_version_with_changelog(client, publisher_headers, admin_headers):
    cap_id = await _create_and_publish(
        client, publisher_headers, admin_headers, "changelog-tool", "1.0.0"
    )
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/versions",
        headers=publisher_headers,
        json={
            "new_version": "1.1.0",
            "change_type": "minor",
            "changelog": "修复下载链接并补充说明",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["version"] == "1.1.0"
    assert body["changelog"] == "修复下载链接并补充说明"
    assert body["status"] == "draft"

    r = await client.get(f"/api/capabilities/{cap_id}/versions", headers=publisher_headers)
    assert r.status_code == 200
    found = next(v for v in r.json() if v["version"] == "1.1.0")
    assert found["changelog"] == "修复下载链接并补充说明"


@pytest.mark.asyncio
async def test_next_version_suggestions(client, publisher_headers, admin_headers):
    cap_id = await _create_and_publish(
        client, publisher_headers, admin_headers, "suggest-tool", "1.2.3"
    )
    r = await client.get(
        f"/api/publish/capabilities/{cap_id}/next-version",
        headers=publisher_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"] == "1.2.3"
    assert body["base"] == "1.2.3"
    assert body["major"] == "2.0.0"
    assert body["minor"] == "1.3.0"
    assert body["patch"] == "1.2.4"

    # 已有更高草稿时，建议应相对最高版本 bump，避免 409
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/versions",
        headers=publisher_headers,
        json={"new_version": "1.2.4", "change_type": "patch"},
    )
    assert r.status_code == 201, r.text
    r = await client.get(
        f"/api/publish/capabilities/{cap_id}/next-version",
        headers=publisher_headers,
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["current"] == "1.2.3"
    assert body["base"] == "1.2.4"
    assert body["patch"] == "1.2.5"
    assert body["minor"] == "1.3.0"
    assert body["major"] == "2.0.0"
