"""A2A 协议测试：Agent Card 发现 / JSON-RPC 任务互调 / 错误码。"""

import pytest


@pytest.mark.asyncio
async def test_well_known_agent_card(client):
    r = await client.get("/.well-known/agent-card.json")
    assert r.status_code == 200, r.text
    card = r.json()
    assert card["protocolVersion"] == "1.0"
    names = {skill["name"] for skill in card["skills"]}
    assert "数字中台分析师" in names


@pytest.mark.asyncio
async def test_agent_discovery(client):
    r = await client.get("/api/a2a/agents")
    assert r.status_code == 200
    cards = r.json()
    assert len(cards) >= 1
    assert all(card["capabilities"]["stateTransitionHistory"] for card in cards)


@pytest.mark.asyncio
async def test_tasks_send_and_get(client, user_headers):
    r = await client.get("/api/capabilities", params={"q": "数字中台"})
    agent_id = r.json()["items"][0]["id"]

    payload = {
        "jsonrpc": "2.0",
        "id": "req-1",
        "method": "tasks/send",
        "params": {
            "id": "client-task-1",
            "message": {"role": "user", "parts": [{"type": "text", "text": "生成上月销售报表"}]},
            "metadata": {"source": "pytest"},
        },
    }
    r = await client.post(f"/api/a2a/agents/{agent_id}/a2a", headers=user_headers, json=payload)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "req-1"
    assert body["result"]["status"]["state"] == "completed"
    assert body["result"]["artifacts"]
    assert "生成上月销售报表" in body["result"]["artifacts"][0]["parts"][0]["text"]
    task_id = body["result"]["id"]

    # REST 查询
    r = await client.get(f"/api/a2a/tasks/{task_id}", headers=user_headers)
    assert r.status_code == 200
    assert r.json()["id"] == task_id

    # JSON-RPC 查询
    r = await client.post(
        f"/api/a2a/agents/{agent_id}/a2a",
        headers=user_headers,
        json={"jsonrpc": "2.0", "id": "req-2", "method": "tasks/get", "params": {"id": task_id}},
    )
    assert r.status_code == 200
    assert r.json()["result"]["status"]["state"] == "completed"


@pytest.mark.asyncio
async def test_tasks_cancel_terminal_task(client, user_headers):
    r = await client.get("/api/capabilities", params={"q": "数字中台"})
    agent_id = r.json()["items"][0]["id"]
    r = await client.post(
        f"/api/a2a/agents/{agent_id}/a2a",
        headers=user_headers,
        json={
            "jsonrpc": "2.0",
            "id": "req-3",
            "method": "tasks/send",
            "params": {
                "id": "client-task-2",
                "message": {"role": "user", "parts": [{"type": "text", "text": "你好"}]},
            },
        },
    )
    task_id = r.json()["result"]["id"]
    r = await client.post(
        f"/api/a2a/agents/{agent_id}/a2a",
        headers=user_headers,
        json={"jsonrpc": "2.0", "id": "req-4", "method": "tasks/cancel", "params": {"id": task_id}},
    )
    assert r.status_code == 200
    assert r.json()["result"]["status"]["state"] == "completed"  # 已终态任务取消后保持终态


@pytest.mark.asyncio
async def test_unknown_method(client, user_headers):
    r = await client.get("/api/capabilities", params={"q": "数字中台"})
    agent_id = r.json()["items"][0]["id"]
    r = await client.post(
        f"/api/a2a/agents/{agent_id}/a2a",
        headers=user_headers,
        json={"jsonrpc": "2.0", "id": "x", "method": "tasks/list"},
    )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32601


@pytest.mark.asyncio
async def test_non_agent_returns_error_code(client, user_headers):
    r = await client.get("/api/capabilities", params={"q": "文件哈希"})
    tool_id = r.json()["items"][0]["id"]
    r = await client.post(
        f"/api/a2a/agents/{tool_id}/a2a",
        headers=user_headers,
        json={
            "jsonrpc": "2.0",
            "id": "x",
            "method": "tasks/send",
            "params": {
                "id": "t1",
                "message": {"role": "user", "parts": [{"type": "text", "text": "hi"}]},
            },
        },
    )
    assert r.status_code == 200
    assert r.json()["error"]["code"] == -32003


@pytest.mark.asyncio
async def test_a2a_requires_auth(client):
    r = await client.get("/api/capabilities", params={"q": "数字中台"})
    agent_id = r.json()["items"][0]["id"]
    r = await client.post(
        f"/api/a2a/agents/{agent_id}/a2a",
        json={"jsonrpc": "2.0", "id": "x", "method": "tasks/send", "params": {}},
    )
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_a2a_records_usage(client, user_headers, admin_headers):
    before = (await client.get("/api/admin/stats", headers=admin_headers)).json()["total_usage"]
    r = await client.get("/api/capabilities", params={"q": "数字中台"})
    agent_id = r.json()["items"][0]["id"]
    await client.post(
        f"/api/a2a/agents/{agent_id}/a2a",
        headers=user_headers,
        json={
            "jsonrpc": "2.0",
            "id": "usage-req",
            "method": "tasks/send",
            "params": {
                "id": "usage-task",
                "message": {"role": "user", "parts": [{"type": "text", "text": "统计"}]},
            },
        },
    )
    after = (await client.get("/api/admin/stats", headers=admin_headers)).json()["total_usage"]
    assert after == before + 1
