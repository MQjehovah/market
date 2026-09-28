"""能力运行规格（设计文档 §3）输出矩阵 + 浏览/详情/检索按 Bearer 用户可见性。"""

import io
import json
import zipfile

import pytest

from test_workflow import _tool_zip


def _mcp_zip(name: str, *, env: dict | None = None, tools: list | None = None) -> bytes:
    files = {
        "mcp.json": json.dumps({"name": name, "description": "运行规格", "version": "1.0.0"}).encode(),
        "connection.json": json.dumps(
            {
                "transport": "stdio",
                "command": "python",
                "args": ["implementation/server.py"],
                "env": env or {},
            }
        ).encode(),
        "tools.json": json.dumps({"tools": tools or []}).encode(),
        "security.json": json.dumps({"sandbox": False}).encode(),
        "implementation/server.py": b"# stub\n",
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


async def _publish(
    client,
    publisher_headers,
    admin_headers,
    name: str,
    *,
    type_: str = "mcp",
    pkg: bytes | None = None,
    distribution: str = "both",
    visibility: str = "internal",
    risk_default: str = "read",
) -> str:
    if pkg is None:
        pkg = _mcp_zip(name) if type_ == "mcp" else _tool_zip(name)
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={
            "name": name,
            "description": "运行规格测试",
            "type": type_,
            "version": "1.0.0",
            "category": "测试",
            "tags": [],
            "visibility": visibility,
            "distribution": distribution,
            "risk_default": risk_default,
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("pkg.zip", pkg, "application/zip")},
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


async def _create_user(
    client, admin_headers, username: str, *, role: str = "user", team: str = ""
) -> dict:
    payload = {
        "username": username,
        "email": f"{username}@example.com",
        "password": "secret123",
        "role": role,
    }
    if team:
        payload["team"] = team
    r = await client.post("/api/admin/users", headers=admin_headers, json=payload)
    assert r.status_code == 201, r.text
    return r.json()


async def _make_token(client, admin_headers, scopes: list[str]) -> str:
    import uuid

    r = await client.post(
        "/api/admin/service-tokens",
        headers=admin_headers,
        json={"name": f"rt-{uuid.uuid4().hex[:8]}", "scopes": scopes},
    )
    assert r.status_code == 201, r.text
    return r.json()["token"]


async def _login(client, username: str) -> dict:
    r = await client.post(
        "/api/auth/login", json={"username": username, "password": "secret123"}
    )
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.mark.asyncio
async def test_runtime_spec_matrix(client, publisher_headers, admin_headers):
    """runtime 字段矩阵：distribution 推导 cloud/local/recommended；清单来自 input_schema。"""
    # both + connection.json（stdio + env 占位 + 2 tools + risk write）
    name = "运行规格连接器"
    cap_id = await _publish(
        client,
        publisher_headers,
        admin_headers,
        name,
        pkg=_mcp_zip(
            name,
            env={"DINGTALK_APP_SECRET": "${DINGTALK_APP_SECRET}", "PLAIN": "secret-value"},
            tools=[{"name": "a"}, {"name": "b"}],
        ),
        risk_default="write",
    )
    r = await client.get(f"/api/capabilities/{cap_id}")
    assert r.status_code == 200, r.text
    rt = r.json()["runtime"]
    assert rt["cloud"] is True and rt["local"] is True and rt["recommended"] == "cloud"
    assert rt["transport"] == "stdio"
    assert rt["command"] == "python"
    assert rt["args"] == ["implementation/server.py"]
    assert rt["env"] == {
        "DINGTALK_APP_SECRET": "${DINGTALK_APP_SECRET}",
        "PLAIN": "${PLAIN}",
    }  # 仅占位，不含明文
    assert rt["tool_count"] == 2
    assert rt["risk"] == "write"
    assert rt["dependencies"] == []

    # 列表 / sync 与详情一致
    r = await client.get("/api/capabilities", params={"q": name})
    item = next(i for i in r.json()["items"] if i["id"] == cap_id)
    assert item["runtime"] == rt
    r = await client.get("/api/capabilities/sync")
    item = next(i for i in r.json() if i["id"] == cap_id)
    assert item["runtime"] == rt

    # local：cloud=False，recommended=local
    local_id = await _publish(
        client, publisher_headers, admin_headers, "本地运行规格", distribution="local"
    )
    rt = (await client.get(f"/api/capabilities/{local_id}")).json()["runtime"]
    assert rt["cloud"] is False and rt["local"] is True and rt["recommended"] == "local"

    # remote：local=False，recommended=cloud
    remote_id = await _publish(
        client, publisher_headers, admin_headers, "云端运行规格", distribution="remote"
    )
    rt = (await client.get(f"/api/capabilities/{remote_id}")).json()["runtime"]
    assert rt["cloud"] is True and rt["local"] is False and rt["recommended"] == "cloud"

    # 无 connection.json（tool）：清单字段缺省，cloud/local 仍由 distribution 推导
    tool_id = await _publish(
        client,
        publisher_headers,
        admin_headers,
        "无清单工具",
        type_="tool",
        distribution="local",
        pkg=_tool_zip("无清单工具"),
    )
    rt = (await client.get(f"/api/capabilities/{tool_id}")).json()["runtime"]
    assert rt == {
        "cloud": False,
        "local": True,
        "recommended": "local",
        "transport": "",
        "command": "",
        "args": [],
        "url": "",
        "env": {},
        "tool_count": 0,
        "risk": "read",
        "dependencies": [],
    }

    # plugin：dependencies 来自 input_schema.components
    from test_plugin import _plugin_zip

    plugin_id = await _publish(
        client,
        publisher_headers,
        admin_headers,
        "运行规格能力包",
        type_="plugin",
        pkg=_plugin_zip(name="运行规格能力包", version="1.0.0"),
    )
    rt = (await client.get(f"/api/capabilities/{plugin_id}")).json()["runtime"]
    deps = {d["name"]: d["type"] for d in rt["dependencies"]}
    assert deps.get("工单客服") == "agent"
    assert deps.get("工单分流") == "skill"
    assert deps.get("jira-demo") == "mcp"


@pytest.mark.asyncio
async def test_browse_and_detail_by_bearer_user(client, publisher_headers, admin_headers):
    """浏览/详情/检索一律按 Bearer 用户可见性；X-Act-As-Sub 头不再切换身份。"""
    # 作者与目标用户同团队（team 可见性需要非空且一致）
    pub_id = (await client.get("/api/auth/me", headers=publisher_headers)).json()["id"]
    r = await client.patch(
        f"/api/admin/users/{pub_id}", headers=admin_headers, json={"team": "alpha"}
    )
    assert r.status_code == 200, r.text
    await _create_user(client, admin_headers, "act-viewer", team="alpha")
    viewer_headers = await _login(client, "act-viewer")

    public_id = await _publish(
        client, publisher_headers, admin_headers, "act公开能力", visibility="internal"
    )
    team_id = await _publish(
        client, publisher_headers, admin_headers, "act团队能力", visibility="team"
    )
    private_id = await _publish(
        client, publisher_headers, admin_headers, "act私有能力", visibility="private"
    )

    # 同团队用户 token：internal + 同 team 可见；他人 private 不可见
    r = await client.get(
        "/api/capabilities",
        params={"include_bricks": True, "page_size": 100},
        headers=viewer_headers,
    )
    assert r.status_code == 200, r.text
    ids = {i["id"] for i in r.json()["items"]}
    assert public_id in ids and team_id in ids and private_id not in ids

    r = await client.get(f"/api/capabilities/{team_id}", headers=viewer_headers)
    assert r.status_code == 200, r.text
    assert r.json()["runtime"]["transport"] == "stdio"
    r = await client.get(f"/api/capabilities/{private_id}", headers=viewer_headers)
    assert r.status_code == 404

    # X-Act-As-Sub 头被忽略：不再 403、也不再切换视角
    ghost = {**viewer_headers, "X-Act-As-Sub": "ghost"}
    r = await client.get(
        "/api/capabilities",
        params={"include_bricks": True, "page_size": 100},
        headers=ghost,
    )
    assert r.status_code == 200
    assert {i["id"] for i in r.json()["items"]} == ids

    # task-search 同样按 Bearer 用户过滤，头不改变结果
    def _hit_names(body: dict) -> set[str]:
        return {
            h["name"]
            for key in ("agents", "skills", "mcps", "plugins", "others")
            for h in body[key]
        }

    r = await client.get(
        "/api/capabilities/task-search", params={"q": "act"}, headers=viewer_headers
    )
    assert r.status_code == 200, r.text
    hit_names = _hit_names(r.json())
    assert "act团队能力" in hit_names and "act私有能力" not in hit_names

    r = await client.get(
        "/api/capabilities/task-search", params={"q": "act"}, headers=ghost
    )
    assert r.status_code == 200
    assert _hit_names(r.json()) == hit_names

    # 服务令牌按绑定用户自身视角（无 team）：仅 internal 可见；带 act-as 头不切换
    token = await _make_token(client, admin_headers, ["gateway", "sync"])
    service = {"Authorization": f"Bearer {token}"}
    r = await client.get(
        "/api/capabilities",
        params={"include_bricks": True, "page_size": 100},
        headers=service,
    )
    assert r.status_code == 200, r.text
    svc_ids = {i["id"] for i in r.json()["items"]}
    assert public_id in svc_ids and team_id not in svc_ids and private_id not in svc_ids
    r = await client.get(f"/api/capabilities/{team_id}", headers=service)
    assert r.status_code == 404

    ghost_svc = {"Authorization": f"Bearer {token}", "X-Act-As-Sub": "act-viewer"}
    r = await client.get(
        "/api/capabilities",
        params={"include_bricks": True, "page_size": 100},
        headers=ghost_svc,
    )
    assert {i["id"] for i in r.json()["items"]} == svc_ids
    r = await client.get(f"/api/capabilities/{team_id}", headers=ghost_svc)
    assert r.status_code == 404

