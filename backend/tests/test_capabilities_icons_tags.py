"""能力头像（上传/展示）与标签筛选/聚合。"""

import pytest

from app.config import get_settings
from test_workflow import _tool_zip

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64
JPG = b"\xff\xd8\xff\xe0" + b"0" * 64
WEBP = b"RIFF\x20\x00\x00\x00WEBP" + b"0" * 32


async def _create_user(client, admin_headers, username: str, role: str = "user") -> dict:
    r = await client.post(
        "/api/admin/users",
        headers=admin_headers,
        json={
            "username": username,
            "email": f"{username}@example.com",
            "password": "secret123",
            "role": role,
        },
    )
    assert r.status_code == 201, r.text
    return r.json()


async def _login(client, username: str) -> dict:
    r = await client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def _publish(
    client,
    publisher_headers,
    admin_headers,
    name: str,
    *,
    tags: list[str] | None = None,
    visibility: str = "internal",
) -> str:
    """带标签发布一个 tool 能力，返回能力 id。"""
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "description": "标签/头像测试",
            "type": "tool",
            "version": "1.0.0",
            "category": "测试",
            "tags": tags or [],
            "visibility": visibility,
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", _tool_zip(name), "application/zip")},
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    return cap_id


def _upload(client, headers, cap_id: str, content: bytes, filename="a.png", content_type="image/png"):
    return client.put(
        f"/api/capabilities/{cap_id}/icon",
        headers=headers,
        files={"file": (filename, content, content_type)},
    )


@pytest.mark.asyncio
async def test_icon_upload_read_replace_delete(client, publisher_headers, admin_headers, user_headers):
    name = "头像能力"
    cap_id = await _publish(client, publisher_headers, admin_headers, name)
    url = f"/api/capabilities/{cap_id}/icon"
    icon_dir = get_settings().icon_path

    # 无图：读取 404，输出 icon_url 为空
    r = await client.get(url)
    assert r.status_code == 404
    r = await client.get(f"/api/capabilities/{cap_id}")
    assert r.json()["icon_url"] == ""

    # 缺少文件 422
    r = await client.put(url, headers=publisher_headers)
    assert r.status_code == 422

    # 声明非图片类型 422
    r = await _upload(client, publisher_headers, cap_id, PNG, content_type="text/plain")
    assert r.status_code == 422

    # 伪造扩展名（内容非图片 magic）422
    r = await _upload(client, publisher_headers, cap_id, b"not-an-image")
    assert r.status_code == 422

    # 超过 256KB → 413
    big = b"\x89PNG\r\n\x1a\n" + b"0" * (256 * 1024)
    r = await _upload(client, publisher_headers, cap_id, big)
    assert r.status_code == 413

    # 非作者/非 admin 403
    r = await _upload(client, user_headers, cap_id, PNG)
    assert r.status_code == 403

    # 作者上传成功：icon_url 输出（带 ?v= 破缓存），文件落盘
    r = await _upload(client, publisher_headers, cap_id, PNG)
    assert r.status_code == 200, r.text
    assert r.json()["icon_url"].startswith(f"{url}?v=")
    assert (icon_dir / f"{cap_id}.png").is_file()

    # 读取 200 + 缓存头 + media type
    r = await client.get(url)
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("image/png")
    assert r.headers["cache-control"] == "public, max-age=3600"
    assert r.content == PNG

    # 列表 / 详情 / sync 均输出 icon_url（带版本参数）
    r = await client.get("/api/capabilities", params={"q": name})
    item = next(i for i in r.json()["items"] if i["id"] == cap_id)
    assert item["icon_url"].startswith(f"{url}?v=")
    r = await client.get("/api/capabilities/sync")
    item = next(i for i in r.json() if i["id"] == cap_id)
    assert item["icon_url"].startswith(f"{url}?v=")

    # 替换为 jpg：旧 png 被清理，只保留一个文件
    r = await _upload(client, publisher_headers, cap_id, JPG, filename="a.jpg", content_type="image/jpeg")
    assert r.status_code == 200, r.text
    files = sorted(p.name for p in icon_dir.glob(f"{cap_id}.*"))
    assert files == [f"{cap_id}.jpg"]
    r = await client.get(url)
    assert r.headers["content-type"].startswith("image/jpeg")

    # webp 也接受
    r = await _upload(client, publisher_headers, cap_id, WEBP, filename="a.webp", content_type="image/webp")
    assert r.status_code == 200, r.text

    # 删除：icon_url 清空，文件删除，GET 404
    r = await client.delete(url, headers=publisher_headers)
    assert r.status_code == 200, r.text
    assert not list(icon_dir.glob(f"{cap_id}.*"))
    r = await client.get(url)
    assert r.status_code == 404
    r = await client.get(f"/api/capabilities/{cap_id}")
    assert r.json()["icon_url"] == ""

    # admin 可管理（重新上传后由 admin 删除）
    r = await _upload(client, admin_headers, cap_id, PNG)
    assert r.status_code == 200, r.text
    r = await client.delete(url, headers=admin_headers)
    assert r.status_code == 200


@pytest.mark.asyncio
async def test_browse_tag_filter_and_meta_tags(client, publisher_headers, admin_headers):
    await _publish(
        client, publisher_headers, admin_headers, "标签甲", tags=["头像标签A", "头像标签B"]
    )
    await _publish(client, publisher_headers, admin_headers, "标签乙", tags=["头像标签A"])
    await _publish(
        client,
        publisher_headers,
        admin_headers,
        "标签丙",
        tags=["plugin-component", "头像标签A"],
    )

    # tag 包含匹配（默认隐藏 plugin-component 子能力）
    r = await client.get("/api/capabilities", params={"tag": "头像标签B", "include_bricks": True})
    assert r.status_code == 200, r.text
    names = {i["name"] for i in r.json()["items"]}
    assert names == {"标签甲"}
    r = await client.get("/api/capabilities", params={"tag": "头像标签A", "include_bricks": True})
    names = {i["name"] for i in r.json()["items"]}
    assert names == {"标签甲", "标签乙"}
    # 不存在的标签 → 空
    r = await client.get("/api/capabilities", params={"tag": "不存在的标签", "include_bricks": True})
    assert r.json()["items"] == []

    # /meta/tags 聚合：published + internal/public，过滤 plugin-component，按次数降序
    r = await client.get("/api/meta/tags")
    assert r.status_code == 200, r.text
    tags = r.json()["tags"]
    assert "头像标签A" in tags and "头像标签B" in tags
    assert "plugin-component" not in tags
    assert tags.index("头像标签A") < tags.index("头像标签B")  # 次数降序（2 > 1）
    assert len(tags) <= 30

    # 草稿标签不参与聚合
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "标签草稿", "type": "tool", "version": "1.0.0", "tags": ["草稿标签"]},
    )
    assert r.status_code == 201, r.text
    r = await client.get("/api/meta/tags")
    assert "草稿标签" not in r.json()["tags"]


@pytest.mark.asyncio
async def test_new_version_inherits_icon(client, publisher_headers, admin_headers):
    """新版本继承头像：v1 设图后发新版本，新行 icon_url 非空且读取 200。"""
    name = "头像继承能力"
    cap_id = await _publish(client, publisher_headers, admin_headers, name)
    r = await _upload(client, publisher_headers, cap_id, PNG)
    assert r.status_code == 200, r.text

    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/versions",
        headers=publisher_headers,
        json={"new_version": "1.0.1", "changelog": "patch"},
    )
    assert r.status_code == 201, r.text
    v2 = r.json()["id"]

    r = await client.get(f"/api/capabilities/{v2}", headers=publisher_headers)
    assert r.status_code == 200, r.text
    assert r.json()["icon_url"].startswith(f"/api/capabilities/{v2}/icon?v=")
    r = await client.get(f"/api/capabilities/{v2}/icon")
    assert r.status_code == 200
    assert r.content == PNG


@pytest.mark.asyncio
async def test_icon_read_respects_visibility(client, publisher_headers, admin_headers):
    """头像读取做可见性：不可见一律 404（不泄露存在性），作者/admin 200。"""
    name = "私有头像能力"
    cap_id = await _publish(
        client, publisher_headers, admin_headers, name, visibility="private"
    )
    r = await _upload(client, publisher_headers, cap_id, PNG)
    assert r.status_code == 200, r.text
    url = f"/api/capabilities/{cap_id}/icon"

    # 匿名 / 其他登录用户：不可见 → 404
    r = await client.get(url)
    assert r.status_code == 404
    await _create_user(client, admin_headers, "icon-outsider")
    outsider = await _login(client, "icon-outsider")
    r = await client.get(url, headers=outsider)
    assert r.status_code == 404

    # 作者 / admin：200
    r = await client.get(url, headers=publisher_headers)
    assert r.status_code == 200
    r = await client.get(url, headers=admin_headers)
    assert r.status_code == 200
