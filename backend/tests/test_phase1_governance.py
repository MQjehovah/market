"""Phase 1 P0：注册表元数据 / 增量同步 / 版本路由 / 服务令牌 / 网关治理。"""

from datetime import datetime, timedelta, timezone

import pytest

from app.config import get_settings
from app.services.capabilities import split_cap_ref
from app.services.gateway_governance import (
    check_circuit,
    check_rate_limit,
    record_failure,
    reset_governance_for_tests,
)
from test_workflow import _publish_capability


def _skill_zip_name(name: str) -> bytes:
    import io
    import json
    import zipfile

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "skill.json",
            json.dumps({"name": name, "description": "d", "version": "1.0.0"}).encode(),
        )
        zf.writestr("SKILL.md", f"# {name}\n".encode())
    return buf.getvalue()


def test_split_cap_ref():
    assert split_cap_ref("device-ops") == ("device-ops", None)
    assert split_cap_ref("device-ops@1.2.3") == ("device-ops", "1.2.3")
    assert split_cap_ref("weird@not-semver") == ("weird@not-semver", None)


@pytest.mark.asyncio
async def test_capability_metadata_and_sync_since(client, publisher_headers, admin_headers):
    name = "治理元数据技能"
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "type": "skill",
            "version": "1.0.0",
            "description": "meta",
            "distribution": "remote",
            "risk_default": "write",
            "data_domain": "工单",
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["distribution"] == "remote"
    assert body["risk_default"] == "write"
    assert body["data_domain"] == "工单"
    cap_id = body["id"]

    await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", _skill_zip_name(name), "application/zip")},
    )
    await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve"},
    )

    r = await client.get("/api/capabilities/sync", headers=admin_headers)
    assert r.status_code == 200
    items = r.json()
    hit = next(i for i in items if i["name"] == name)
    assert hit["distribution"] == "remote"
    assert hit["risk_default"] == "write"
    assert hit["data_domain"] == "工单"
    assert hit["updated_at"]

    future = (datetime.now(timezone.utc) + timedelta(days=1)).replace(tzinfo=None).isoformat()
    r = await client.get(f"/api/capabilities/sync?since={future}", headers=admin_headers)
    assert r.status_code == 200
    assert all(i["name"] != name for i in r.json())

    past = "2020-01-01T00:00:00"
    r = await client.get(f"/api/capabilities/sync?since={past}", headers=admin_headers)
    assert r.status_code == 200
    assert any(i["name"] == name for i in r.json())


@pytest.mark.asyncio
async def test_service_token_auth(client, admin_headers):
    r = await client.post(
        "/api/admin/service-tokens",
        headers=admin_headers,
        json={"name": "agent-runner", "scopes": ["runtime", "gateway", "sync"]},
    )
    assert r.status_code == 201, r.text
    created = r.json()
    token = created["token"]
    assert token.startswith("mkt_svc_")
    assert created["token_prefix"]

    auth = {"Authorization": f"Bearer {token}"}
    r = await client.get("/api/publish/my", headers=auth)
    assert r.status_code == 200

    r = await client.post(
        f"/api/admin/service-tokens/{created['id']}/revoke",
        headers=admin_headers,
    )
    assert r.status_code == 200
    assert r.json()["revoked"] is True

    r = await client.get("/api/publish/my", headers=auth)
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_gateway_rate_limit_and_circuit():
    reset_governance_for_tests()
    settings = get_settings()
    old_limit = settings.mcp_gateway_rate_limit_per_minute
    old_thresh = settings.mcp_gateway_circuit_fail_threshold
    settings.mcp_gateway_rate_limit_per_minute = 3
    settings.mcp_gateway_circuit_fail_threshold = 2
    settings.mcp_gateway_circuit_cooldown_seconds = 30
    try:
        assert check_rate_limit("user:t1")[0]
        assert check_rate_limit("user:t1")[0]
        assert check_rate_limit("user:t1")[0]
        ok, reason = check_rate_limit("user:t1")
        assert not ok
        assert "限流" in reason

        assert check_circuit("mcp:x")[0]
        record_failure("mcp:x")
        assert check_circuit("mcp:x")[0]
        record_failure("mcp:x")
        ok, reason = check_circuit("mcp:x")
        assert not ok
        assert "熔断" in reason
    finally:
        settings.mcp_gateway_rate_limit_per_minute = old_limit
        settings.mcp_gateway_circuit_fail_threshold = old_thresh
        reset_governance_for_tests()


@pytest.mark.asyncio
async def test_resolve_versioned_runtime_name(client, publisher_headers, admin_headers):
    name = "版本钉技能"
    await _publish_capability(
        client, publisher_headers, admin_headers, name, "skill", _skill_zip_name(name)
    )
    # 再发 1.0.1
    r = await client.get("/api/publish/my", headers=publisher_headers)
    cap = next(c for c in r.json() if c["name"] == name and c["status"] == "published")
    r = await client.post(
        f"/api/publish/capabilities/{cap['id']}/versions",
        headers=publisher_headers,
        json={"new_version": "1.0.1", "changelog": "patch"},
    )
    assert r.status_code == 201, r.text
    v2 = r.json()["id"]
    await client.post(
        f"/api/publish/capabilities/{v2}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", _skill_zip_name(name), "application/zip")},
    )
    await client.post(f"/api/publish/capabilities/{v2}/submit", headers=publisher_headers)
    await client.post(
        f"/api/admin/capabilities/{v2}/review",
        headers=admin_headers,
        json={"action": "approve"},
    )

    r = await client.post(
        f"/api/runtime/skills/{name}@1.0.0/activate",
        headers=admin_headers,
        json={"context": ""},
    )
    assert r.status_code == 200, r.text
    assert r.json()["capability"]["version"] == "1.0.0"

    r = await client.post(
        f"/api/runtime/skills/{name}/activate",
        headers=admin_headers,
        json={"context": ""},
    )
    assert r.status_code == 200
    assert r.json()["capability"]["version"] == "1.0.1"
