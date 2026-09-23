"""平台轨：能力元数据扩展 / 同步 API 网关关联。

since 增量语义（不折叠、非法值 422）由 gitlab 生产实现与
``test_phase1_governance.py::test_capability_metadata_and_sync_since`` 覆盖，
本文件不再重复断言，只保留 M1 真增量：sync 的 mcp `gateway` 关联与绑定 CRUD。
"""

import json

import pytest

from app.config import get_settings
from test_workflow import _publish_capability, _tool_zip, _zip

API = "/api"


def _mcp_zip(name: str) -> bytes:
    return _zip(
        {
            "mcp.json": json.dumps(
                {"name": name, "description": "测试 MCP", "version": "1.0.0"}
            ).encode("utf-8"),
            "connection.json": json.dumps(
                {
                    "transport": "stdio",
                    "command": "python",
                    "args": ["implementation/server.py"],
                }
            ).encode("utf-8"),
            "tools.json": json.dumps(
                {"tools": [{"name": "ping", "description": "ping"}]}
            ).encode("utf-8"),
            "security.json": json.dumps({"sandbox": False}).encode("utf-8"),
            "implementation/server.py": b"# test mcp server\n",
        }
    )


def _server_payload(name: str, capability_id: str = "", enabled: bool = True) -> dict:
    return {
        "name": name,
        "description": "测试网关服务",
        "transport": "stdio",
        "command": "python",
        "args": [],
        "capability_id": capability_id,
        "enabled": enabled,
    }


async def _create_capability(client, headers, name: str, type_: str = "tool", **extra) -> dict:
    payload = {
        "name": name,
        "description": "测试",
        "type": type_,
        "version": "1.0.0",
        "category": "测试",
        "tags": [],
        "visibility": "internal",
    }
    payload.update(extra)
    r = await client.post(f"{API}/publish/capabilities", headers=headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def _publish(client, publisher_headers, admin_headers, cap_id: str, pkg: bytes) -> dict:
    r = await client.post(
        f"{API}/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", pkg, "application/zip")},
    )
    assert r.status_code == 200, r.text
    r = await client.post(f"{API}/publish/capabilities/{cap_id}/submit", headers=publisher_headers)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"{API}/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text
    return r.json()


async def _sync(client, **params) -> list[dict]:
    r = await client.get(f"{API}/capabilities/sync", params=params)
    assert r.status_code == 200, r.text
    return r.json()


@pytest.mark.asyncio
async def test_metadata_roundtrip_create_update_sync(client, publisher_headers, admin_headers):
    """三字段：创建/更新往返，非法值与超长校验，发布后 sync 可见。"""
    cap = await _create_capability(
        client,
        publisher_headers,
        "meta-tool",
        "tool",
        distribution="remote",
        risk_default="write",
        data_domain="客户",
    )
    assert cap["distribution"] == "remote"
    assert cap["risk_default"] == "write"
    assert cap["data_domain"] == "客户"

    r = await client.put(
        f"{API}/publish/capabilities/{cap['id']}",
        headers=publisher_headers,
        json={"distribution": "local", "risk_default": "destructive", "data_domain": "财务"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["distribution"], body["risk_default"], body["data_domain"]) == (
        "local",
        "destructive",
        "财务",
    )

    # 不传时走默认值
    defaulted = await _create_capability(client, publisher_headers, "meta-default", "tool")
    assert (defaulted["distribution"], defaulted["risk_default"], defaulted["data_domain"]) == (
        "both",
        "read",
        "",
    )

    # 非法枚举 / 超长自由文本（gitlab 口径 max_length=64）
    r = await client.post(
        f"{API}/publish/capabilities",
        headers=publisher_headers,
        json={"name": "meta-bad", "type": "tool", "version": "1.0.0", "distribution": "cloud"},
    )
    assert r.status_code == 422
    r = await client.post(
        f"{API}/publish/capabilities",
        headers=publisher_headers,
        json={"name": "meta-long", "type": "tool", "version": "1.0.0", "data_domain": "x" * 65},
    )
    assert r.status_code == 422
    r = await client.put(
        f"{API}/publish/capabilities/{cap['id']}",
        headers=publisher_headers,
        json={"risk_default": "danger"},
    )
    assert r.status_code == 422

    # 发布后同步目录带三字段
    await _publish(client, publisher_headers, admin_headers, cap["id"], _tool_zip("meta-tool"))
    items = {i["name"]: i for i in await _sync(client)}
    item = items["meta-tool"]
    assert item["distribution"] == "local"
    assert item["risk_default"] == "destructive"
    assert item["data_domain"] == "财务"
    # 非 mcp 类型不带 gateway 键
    assert "gateway" not in item


@pytest.mark.asyncio
async def test_sync_gateway_binding_and_require_token(
    client, publisher_headers, admin_headers, monkeypatch
):
    """mcp 能力绑定网关注册行后，sync 输出 gateway；require_token 跟随 settings。"""
    cap_id = await _publish_capability(
        client, publisher_headers, admin_headers, "gw-bound", "mcp", _mcp_zip("gw-bound")
    )
    r = await client.post(
        f"{API}/admin/mcp-gateway/servers",
        headers=admin_headers,
        json=_server_payload("gw-bound", cap_id),
    )
    assert r.status_code == 201, r.text
    assert r.json()["capability_id"] == cap_id

    items = {i["name"]: i for i in await _sync(client)}
    assert items["gw-bound"]["gateway"] == {
        "name": "gw-bound",
        "transport": "stdio",
        "require_token": False,
        "stream_url": "/api/mcp-gateway/relay/gw-bound/stream",
        "sse_url": "/api/mcp-gateway/relay/gw-bound/sse",
        "service_url": "/api/mcp-gateway/gw-bound/stream",
    }

    # 打开 require_token 后同步结果变化
    monkeypatch.setattr(get_settings(), "mcp_gateway_require_token", True)
    items = {i["name"]: i for i in await _sync(client)}
    assert items["gw-bound"]["gateway"]["require_token"] is True


@pytest.mark.asyncio
async def test_sync_gateway_null_and_name_fallback(client, publisher_headers, admin_headers):
    """未绑定且无同名 server → null；同名 enabled 行回退；disabled 不回退。"""
    await _publish_capability(
        client, publisher_headers, admin_headers, "gw-none", "mcp", _mcp_zip("gw-none")
    )

    # 同名 enabled 的 server 作为回退
    r = await client.post(
        f"{API}/admin/mcp-gateway/servers",
        headers=admin_headers,
        json=_server_payload("gw-fallback"),
    )
    assert r.status_code == 201, r.text
    await _publish_capability(
        client, publisher_headers, admin_headers, "gw-fallback", "mcp", _mcp_zip("gw-fallback")
    )

    # 同名但 disabled 的 server 不回退
    r = await client.post(
        f"{API}/admin/mcp-gateway/servers",
        headers=admin_headers,
        json=_server_payload("gw-off", enabled=False),
    )
    assert r.status_code == 201, r.text
    await _publish_capability(
        client, publisher_headers, admin_headers, "gw-off", "mcp", _mcp_zip("gw-off")
    )

    items = {i["name"]: i for i in await _sync(client)}
    assert items["gw-none"]["gateway"] is None
    assert items["gw-off"]["gateway"] is None
    gateway = items["gw-fallback"]["gateway"]
    assert gateway is not None
    assert gateway["name"] == "gw-fallback"
    assert gateway["stream_url"] == "/api/mcp-gateway/relay/gw-fallback/stream"
    assert gateway["service_url"] == "/api/mcp-gateway/gw-fallback/stream"


@pytest.mark.asyncio
async def test_gateway_binding_conflicts(client, publisher_headers, admin_headers):
    """重复绑定 409；不存在 / 非 mcp 400；更新自身不冲突，可解绑。"""
    cap_id = await _publish_capability(
        client, publisher_headers, admin_headers, "bind-cap", "mcp", _mcp_zip("bind-cap")
    )
    r = await client.post(
        f"{API}/admin/mcp-gateway/servers",
        headers=admin_headers,
        json=_server_payload("bind-a", cap_id),
    )
    assert r.status_code == 201, r.text
    sid = r.json()["id"]

    # 另一个 server 重复绑定同一 capability → 409
    r = await client.post(
        f"{API}/admin/mcp-gateway/servers",
        headers=admin_headers,
        json=_server_payload("bind-b", cap_id),
    )
    assert r.status_code == 409

    # 绑定不存在的能力 → 400
    r = await client.post(
        f"{API}/admin/mcp-gateway/servers",
        headers=admin_headers,
        json=_server_payload("bind-x", "no-such-capability"),
    )
    assert r.status_code == 400

    # 绑定非 mcp 能力 → 400
    tool_cap = await _create_capability(client, publisher_headers, "bind-tool", "tool")
    r = await client.post(
        f"{API}/admin/mcp-gateway/servers",
        headers=admin_headers,
        json=_server_payload("bind-tool-srv", tool_cap["id"]),
    )
    assert r.status_code == 400

    # 更新自身保持绑定不冲突
    r = await client.put(
        f"{API}/admin/mcp-gateway/servers/{sid}",
        headers=admin_headers,
        json=_server_payload("bind-a", cap_id),
    )
    assert r.status_code == 200, r.text
    assert r.json()["capability_id"] == cap_id

    # 解绑后 sync 回退为 null（无同名 server）
    r = await client.put(
        f"{API}/admin/mcp-gateway/servers/{sid}",
        headers=admin_headers,
        json=_server_payload("bind-a"),
    )
    assert r.status_code == 200, r.text
    assert r.json()["capability_id"] == ""
    items = {i["name"]: i for i in await _sync(client)}
    assert items["bind-cap"]["gateway"] is None
