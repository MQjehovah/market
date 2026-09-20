"""能力包文件预览 API。"""

import io
import json
import zipfile

import pytest


def _skill_package(name: str) -> bytes:
    files = {
        "skill.json": json.dumps(
            {"name": name, "description": "预览测试", "version": "1.0.0"}
        ).encode("utf-8"),
        "SKILL.md": "# Hello\n\npreview body\n".encode("utf-8"),
        "references/note.txt": "note content".encode("utf-8"),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_package_tree_and_file_preview(client, publisher_headers, admin_headers):
    name = "预览测试技能"
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "description": "文件预览",
            "type": "skill",
            "version": "1.0.0",
            "category": "测试",
            "tags": ["preview"],
            "visibility": "internal",
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]

    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("skill.zip", _skill_package(name), "application/zip")},
    )
    assert r.status_code == 200, r.text

    r = await client.get(f"/api/capabilities/{cap_id}/package/tree", headers=publisher_headers)
    assert r.status_code == 200, r.text
    tree = r.json()
    paths = {f["path"] for f in tree["files"]}
    assert "SKILL.md" in paths
    assert "skill.json" in paths
    assert "references/note.txt" in paths

    r = await client.get(
        f"/api/capabilities/{cap_id}/package/file",
        headers=admin_headers,
        params={"path": "SKILL.md"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["binary"] is False
    assert "preview body" in body["content"]
    assert body["path"] == "SKILL.md"

    r = await client.get(
        f"/api/capabilities/{cap_id}/package/file",
        headers=admin_headers,
        params={"path": "../etc/passwd"},
    )
    assert r.status_code == 400
