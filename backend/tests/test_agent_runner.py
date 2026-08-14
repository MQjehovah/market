"""Agent 真实执行测试：LLM 已配置时任务/工具循环走真实路径（mock 掉 chat 完成接口）。"""

import pytest

from app.services import agent_runner
from test_workflow import _agent_zip, _publish_capability


@pytest.mark.asyncio
async def test_runtime_task_real_llm(client, publisher_headers, admin_headers, monkeypatch):
    persona = "llm-persona"
    await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona)
    )

    async def fake_chat(messages, tools):
        return {"choices": [{"message": {"role": "assistant", "content": "真实回答：你好"}}]}

    monkeypatch.setattr(agent_runner, "is_llm_configured", lambda: True)
    monkeypatch.setattr(agent_runner, "_chat_completion", fake_chat)

    r = await client.post(
        f"/api/runtime/agents/{persona}/tasks",
        headers=publisher_headers,
        json={"task": "打个招呼"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "llm"
    assert body["output"] == "真实回答：你好"
    assert body["agent"] == persona
    assert body["tool_calls"] == 0


@pytest.mark.asyncio
async def test_agent_loop_executes_tool_calls(client, publisher_headers, admin_headers, monkeypatch):
    persona = "llm-loop-persona"
    await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona)
    )

    calls = {"n": 0}

    async def fake_chat(messages, tools):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "type": "function",
                                    "function": {
                                        "name": "不存在的工具",
                                        "arguments": '{"text": "hi"}',
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "工具结果已处理"}}]}

    monkeypatch.setattr(agent_runner, "is_llm_configured", lambda: True)
    monkeypatch.setattr(agent_runner, "_chat_completion", fake_chat)

    r = await client.post(
        f"/api/runtime/agents/{persona}/tasks",
        headers=publisher_headers,
        json={"task": "调用工具"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["mode"] == "llm"
    assert body["output"] == "工具结果已处理"
    assert body["tool_calls"] == 1
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_a2a_task_real_llm(client, publisher_headers, admin_headers, monkeypatch):
    persona = "llm-a2a-persona"
    await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona)
    )

    async def fake_chat(messages, tools):
        return {"choices": [{"message": {"role": "assistant", "content": "A2A真实回答"}}]}

    monkeypatch.setattr(agent_runner, "is_llm_configured", lambda: True)
    monkeypatch.setattr(agent_runner, "_chat_completion", fake_chat)

    r = await client.get(f"/api/capabilities?q={persona}&type=agent&page_size=5")
    cap_id = r.json()["items"][0]["id"]
    r = await client.post(
        f"/api/a2a/agents/{cap_id}/a2a",
        headers=publisher_headers,
        json={
            "jsonrpc": "2.0",
            "id": "t1",
            "method": "tasks/send",
            "params": {
                "id": "web-task-1",
                "message": {"role": "user", "parts": [{"type": "text", "text": "你好"}]},
            },
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["result"]["status"]["state"] == "completed"
    text = body["result"]["status"]["message"]["parts"][0]["text"]
    assert "A2A真实回答" in text
    assert "[执行模式] llm" in text
