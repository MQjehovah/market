"""rule / command / hook 在线编辑，以及自定义页启用开关。"""

import io
import json
import zipfile

import pytest

from test_workflow import _publish_capability


def _rule_zip(name: str, body: str = "") -> bytes:
    files = {
        "rule.json": json.dumps(
            {
                "name": name,
                "description": "测试规则",
                "version": "1.0.0",
                "alwaysApply": False,
                "globs": "**/*.py",
            }
        ).encode("utf-8"),
        "RULE.mdc": (body or f"# {name}\n\nprefer existing patterns.\n").encode("utf-8"),
        "references/note.md": b"ref",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


def _command_zip(name: str) -> bytes:
    files = {
        "command.json": json.dumps(
            {"name": name, "description": "测试命令", "version": "1.0.0"}
        ).encode("utf-8"),
        "COMMAND.md": f"# {name}\n\n1. do it\n".encode("utf-8"),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


def _hook_zip(name: str) -> bytes:
    files = {
        "hook.json": json.dumps(
            {"name": name, "description": "测试 hooks", "version": "1.0.0"}
        ).encode("utf-8"),
        "hooks.json": json.dumps(
            {"version": 1, "hooks": {"sessionStart": [{"command": "./scripts/hi.sh"}]}},
            ensure_ascii=False,
        ).encode("utf-8"),
        "scripts/hi.sh": b"#!/bin/sh\necho hi\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


@pytest.mark.asyncio
async def test_rule_edit_save_new_version(client, publisher_headers, admin_headers):
    name = "editable-rule"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "rule", _rule_zip(name)
    )
    r = await client.get(f"/api/rules/{name}/edit", headers=publisher_headers)
    assert r.status_code == 200, r.text
    assert "prefer existing" in r.json()["body"]
    r = await client.put(
        f"/api/rules/{name}/edit",
        headers=publisher_headers,
        json={"body": "# v2\n\nnew rule body", "always_apply": True, "globs": "**/*.ts"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["capability"]["version"] == "1.0.1"
    assert body["capability"]["status"] == "draft"
    assert body["always_apply"] is True
    draft_id = body["capability"]["id"]
    r = await client.get(
        f"/api/publish/capabilities/{draft_id}/artifact/download",
        headers=publisher_headers,
    )
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        assert "references/note.md" in zf.namelist()
        assert "new rule body" in zf.read("RULE.mdc").decode("utf-8")


@pytest.mark.asyncio
async def test_command_and_hook_edit(client, publisher_headers, admin_headers):
    cmd = "editable-command"
    hook = "editable-hook"
    await _publish_capability(
        client, publisher_headers, admin_headers, cmd, "command", _command_zip(cmd)
    )
    await _publish_capability(
        client, publisher_headers, admin_headers, hook, "hook", _hook_zip(hook)
    )
    r = await client.put(
        f"/api/commands/{cmd}/edit",
        headers=publisher_headers,
        json={"body": "# ship\n\nnew command"},
    )
    assert r.status_code == 200, r.text
    assert r.json()["capability"]["version"] == "1.0.1"
    r = await client.put(
        f"/api/hooks/{hook}/edit",
        headers=publisher_headers,
        json={
            "hooks_json": json.dumps(
                {"version": 1, "hooks": {"sessionEnd": [{"command": "./scripts/hi.sh"}]}}
            )
        },
    )
    assert r.status_code == 200, r.text
    assert "sessionEnd" in r.json()["hooks_json"]


@pytest.mark.asyncio
async def test_my_capability_enable_toggle(
    client, publisher_headers, admin_headers, user_headers
):
    name = "toggle-rule"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "rule", _rule_zip(name)
    )
    r = await client.get("/api/capabilities", params={"q": name, "type": "rule"})
    cap_id = r.json()["items"][0]["id"]
    r = await client.post(
        "/api/my/capabilities", headers=user_headers, json={"capability_id": cap_id}
    )
    assert r.status_code == 201, r.text
    r = await client.get("/api/my/capabilities?scope=added", headers=user_headers)
    row = next(c for c in r.json() if c["id"] == cap_id)
    assert row.get("enabled") is True

    r = await client.patch(
        f"/api/my/capabilities/{cap_id}",
        headers=user_headers,
        json={"enabled": False},
    )
    assert r.status_code == 200, r.text
    r = await client.get("/api/my/capabilities?scope=added", headers=user_headers)
    row = next(c for c in r.json() if c["id"] == cap_id)
    assert row.get("enabled") is False

    await client.post(
        f"/api/capabilities/{cap_id}/install-policy",
        headers=admin_headers,
        json={"install_policy": "required"},
    )
    r = await client.patch(
        f"/api/my/capabilities/{cap_id}",
        headers=user_headers,
        json={"enabled": False},
    )
    assert r.status_code == 422
    assert "必装" in r.json()["detail"]


@pytest.mark.asyncio
async def test_install_rule_writes_config_dir(tmp_path):
    import sys
    from pathlib import Path as P

    consumer = P(__file__).resolve().parents[2] / "consumer"
    sys.path.insert(0, str(consumer))
    from cap.installer import _install_dir_kind

    class FakeClient:
        base_url = "http://example"

        def download(self, name, version="", cap_type=""):
            return _rule_zip(name), {"x-capability-version": "1.0.0"}

    dest = tmp_path / "config"
    out = _install_dir_kind(
        FakeClient(),
        "toggle-rule",
        cap_type="rule",
        subdir="rules",
        target=dest,
        skip_meta=("rule.json",),
    )
    assert (dest / "rules" / "toggle-rule" / "RULE.mdc").is_file()
    assert not (dest / "rules" / "toggle-rule" / "rule.json").exists()
    assert P(out["path"]).name == "toggle-rule"
