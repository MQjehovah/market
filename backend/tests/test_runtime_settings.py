"""运行时可变配置：覆盖/恢复 + 热重载 + 管理端接口。"""

import pytest

from app.config import get_settings
from app.services import runtime_settings


@pytest.fixture
def rs_file(tmp_path, monkeypatch):
    p = tmp_path / "runtime_settings.json"
    monkeypatch.setattr(runtime_settings, "_path", lambda: p)
    return p


def test_override_and_reset(rs_file):
    base = get_settings().agent_max_iterations
    assert runtime_settings.agent_max_iterations() == base
    runtime_settings.save({"agent_max_iterations": 100})
    assert runtime_settings.agent_max_iterations() == 100
    # 读文件即最新（热重载）
    assert rs_file.is_file()
    # 传 None 恢复默认
    runtime_settings.save({"agent_max_iterations": None})
    assert runtime_settings.agent_max_iterations() == base


def test_get_int_bad_value_falls_back(rs_file):
    rs_file.write_text('{"agent_max_iterations": "oops"}', encoding="utf-8")
    assert runtime_settings.agent_max_iterations() == get_settings().agent_max_iterations


@pytest.mark.asyncio
async def test_admin_runtime_settings_endpoint(client, admin_headers, user_headers, rs_file):
    r = await client.get("/api/admin/runtime-settings", headers=admin_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["overridden"] is False
    assert body["agent_max_iterations"] == body["default"]

    r = await client.put(
        "/api/admin/runtime-settings",
        headers=admin_headers,
        json={"agent_max_iterations": 77},
    )
    assert r.status_code == 200, r.text
    assert r.json()["agent_max_iterations"] == 77
    assert r.json()["overridden"] is True

    # 恢复默认
    r = await client.put(
        "/api/admin/runtime-settings",
        headers=admin_headers,
        json={"agent_max_iterations": None},
    )
    assert r.status_code == 200
    assert r.json()["overridden"] is False

    # 非管理员拒绝
    r = await client.get("/api/admin/runtime-settings", headers=user_headers)
    assert r.status_code == 403
