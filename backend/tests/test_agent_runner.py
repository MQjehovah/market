"""Agent 真实执行测试：LLM 已配置时任务/工具循环走真实路径（mock 掉 chat 完成接口）。"""

import io
import json
import zipfile

import pytest

from app.services import agent_runner
from test_workflow import _agent_zip, _publish_capability


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for fn, data in files.items():
            zf.writestr(fn, data)
    return buf.getvalue()


def _skill_zip(name: str, marker: str) -> bytes:
    return _zip(
        {
            "skill.json": json.dumps({"name": name, "description": "测试技能"}).encode("utf-8"),
            "SKILL.md": f"# {name}\n\n{marker}\n".encode("utf-8"),
        }
    )


def _agent_with_deps_zip(name: str, deps: list[dict]) -> bytes:
    return _zip(
        {
            "agent.json": json.dumps({"name": name, "description": "测试人设"}).encode("utf-8"),
            "PROMPT.md": f"你是{name}，负责测试。".encode("utf-8"),
            "dependencies.json": json.dumps(deps, ensure_ascii=False).encode("utf-8"),
        }
    )


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
        headers=admin_headers,
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
        headers=admin_headers,
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
        headers=admin_headers,
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


@pytest.mark.asyncio
async def test_agent_skill_tool_activation(client, publisher_headers, admin_headers, monkeypatch):
    """绑定技能时执行器必须暴露 skill 工具：LLM 调用后返回 SKILL.md 指引并继续多轮。"""
    skill_name = "llm-skill"
    marker = "必须按红灯-绿灯-重构执行"
    await _publish_capability(
        client, publisher_headers, admin_headers, skill_name, "skill", _skill_zip(skill_name, marker)
    )
    persona = "llm-skill-agent"
    await _publish_capability(
        client,
        publisher_headers,
        admin_headers,
        persona,
        "agent",
        _agent_with_deps_zip(persona, [{"name": skill_name, "type": "skill", "version": "1.0.0"}]),
    )

    calls = []

    async def fake_chat(messages, tools):
        calls.append((list(messages), list(tools)))
        if len(calls) == 1:
            return {
                "choices": [
                    {
                        "message": {
                            "role": "assistant",
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_skill",
                                    "type": "function",
                                    "function": {
                                        "name": "skill",
                                        "arguments": json.dumps({"skill": skill_name}),
                                    },
                                }
                            ],
                        }
                    }
                ]
            }
        return {"choices": [{"message": {"role": "assistant", "content": "已按技能完成"}}]}

    monkeypatch.setattr(agent_runner, "is_llm_configured", lambda: True)
    monkeypatch.setattr(agent_runner, "_chat_completion", fake_chat)

    r = await client.post(
        f"/api/runtime/agents/{persona}/tasks",
        headers=admin_headers,
        json={"task": "做一次开发"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["tool_calls"] == 1
    assert body["output"] == "已按技能完成"
    # 执行轨迹：包含 skill 调用步骤与返回结果步骤
    assert any(s["kind"] == "call" and s["name"] == "skill" for s in body.get("steps", []))
    assert any(s["kind"] == "result" for s in body.get("steps", []))

    # 第一轮的工具定义里必须包含 skill 函数
    _, tools = calls[0]
    assert any(t["function"]["name"] == "skill" for t in tools)
    # 第二轮消息里应包含 SKILL.md 指引内容
    messages = calls[1][0]
    tool_msg = [m for m in messages if m["role"] == "tool"]
    assert tool_msg and marker in tool_msg[-1]["content"]
