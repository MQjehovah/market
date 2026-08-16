"""技能在线编辑：读取 SKILL.md、保存新版本草稿、附属文件保留。"""

import io
import json
import zipfile

import pytest


def _skill_zip(name: str, skill_md: str = "") -> bytes:
    files = {
        "skill.json": json.dumps(
            {"name": name, "description": "测试技能", "version": "1.0.0", "category": "数据分析"}
        ).encode("utf-8"),
        "SKILL.md": (skill_md or f"# {name}\n\n原始技能内容。").encode("utf-8"),
        "references/note.md": "参考文档".encode("utf-8"),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


async def _publish_skill(client, publisher_headers, admin_headers, name: str) -> str:
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "description": "测试技能",
            "type": "skill",
            "version": "1.0.0",
            "category": "数据分析",
            "tags": ["测试"],
            "visibility": "internal",
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("skill.zip", _skill_zip(name), "application/zip")},
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    assert r.status_code == 200
    r = await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    return cap_id


@pytest.mark.asyncio
async def test_skill_edit_read_and_save_new_version(client, publisher_headers, admin_headers):
    name = "editable-skill"
    await _publish_skill(client, publisher_headers, admin_headers, name)

    # 读取编辑态
    r = await client.get(f"/api/skills/{name}/edit", headers=publisher_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability"]["version"] == "1.0.0"
    assert "原始技能内容" in body["skill_md"]
    assert any(f["path"] == "references/note.md" for f in body["files"])

    # 保存 → 自动 patch+1 新版本草稿
    r = await client.put(
        f"/api/skills/{name}/edit",
        headers=publisher_headers,
        json={
            "skill_md": "# 新版本\n\n在线修改后的技能内容。",
            "description": "更新后的描述",
            "tags": ["测试", "在线编辑"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability"]["version"] == "1.0.1"
    assert body["capability"]["status"] == "draft"
    assert "在线修改后的技能内容" in body["skill_md"]
    assert any(f["path"] == "references/note.md" for f in body["files"])
    draft_id = body["capability"]["id"]

    # 草稿包内 SKILL.md 已更新、附属文件保留
    r = await client.get(
        f"/api/publish/capabilities/{draft_id}/artifact/download",
        headers=publisher_headers,
    )
    assert r.status_code == 200, r.text
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        names = set(zf.namelist())
        assert "references/note.md" in names
        assert "在线修改后的技能内容" in zf.read("SKILL.md").decode("utf-8")

    # 草稿可提交审核并发布
    r = await client.post(f"/api/publish/capabilities/{draft_id}/submit", headers=publisher_headers)
    assert r.status_code == 200
    r = await client.post(
        f"/api/admin/capabilities/{draft_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["status"] == "published"


@pytest.mark.asyncio
async def test_skill_edit_requires_owner(client, publisher_headers, admin_headers, user_headers):
    name = "owner-skill"
    await _publish_skill(client, publisher_headers, admin_headers, name)

    # 他人不能保存新版本
    r = await client.put(
        f"/api/skills/{name}/edit",
        headers=user_headers,
        json={"skill_md": "篡改内容"},
    )
    assert r.status_code == 403
