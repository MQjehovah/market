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
            "skill.json": json.dumps(
                {"name": name, "description": "测试技能", "version": "1.0.0"}
            ).encode("utf-8"),
            "SKILL.md": f"# {name}\n\n{marker}\n".encode("utf-8"),
        }
    )


def _agent_with_deps_zip(name: str, deps: list[dict]) -> bytes:
    return _zip(
        {
            "plugin.json": json.dumps(
                {"name": name, "description": "测试人设", "dependencies": deps},
                ensure_ascii=False,
            ).encode("utf-8"),
            f"agents/{name}.md": (
                f"---\nname: {name}\ndescription: 测试人设\n---\n你是{name}，负责测试。"
            ).encode("utf-8"),
        }
    )


@pytest.mark.asyncio
async def test_runtime_task_real_llm(client, publisher_headers, admin_headers, monkeypatch):
    persona = "llm-persona"
    await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona)
    )

    seen = {}

    async def fake_chat(messages, tools):
        seen["ctx"] = agent_runner._llm_ctx.get()
        return {"choices": [{"message": {"role": "assistant", "content": "真实回答：你好"}}]}

    monkeypatch.setattr(agent_runner, "is_llm_configured", lambda: True)
    monkeypatch.setattr(agent_runner, "_chat_completion", fake_chat)
    monkeypatch.setattr(
        "app.services.router_key.personal_gateway_credentials",
        lambda user: ("http://gw/v1", "sk-test", "model"),
    )

    r = await client.post(
        f"/api/runtime/agents/{persona}/tasks",
        headers=admin_headers,
        json={"task": "打个招呼"},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["mode"] == "llm"
    assert body["output"] == "真实回答：你好"
    assert body["agent"] == persona
    assert body["tool_calls"] == 0
    assert seen["ctx"] == ("http://gw/v1", "sk-test", "model")


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
    monkeypatch.setattr(
        "app.services.router_key.personal_gateway_credentials",
        lambda user: ("http://gw/v1", "sk-test", "model"),
    )

    r = await client.post(
        f"/api/runtime/agents/{persona}/tasks",
        headers=admin_headers,
        json={"task": "调用工具"},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["mode"] == "llm"
    assert body["output"] == "工具结果已处理"
    assert body["tool_calls"] == 1
    assert calls["n"] == 2
    # 默认精简信封：不回传 steps/runtime，避免下游压缩成 "..."
    assert "steps" not in body
    assert "runtime" not in body

    r_trace = await client.post(
        f"/api/runtime/agents/{persona}/tasks?include_trace=true",
        headers=admin_headers,
        json={"task": "调用工具"},
    )
    assert r_trace.status_code == 200, r_trace.text
    traced = r_trace.json()["result"]
    assert isinstance(traced.get("steps"), list) and len(traced["steps"]) >= 1
    assert isinstance(traced.get("runtime"), dict)


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
    assert text.strip() == "A2A真实回答"
    assert body["result"]["metadata"]["mode"] == "llm"


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
    monkeypatch.setattr(
        "app.services.router_key.personal_gateway_credentials",
        lambda user: ("http://gw/v1", "sk-test", "model"),
    )

    r = await client.post(
        f"/api/runtime/agents/{persona}/tasks?include_trace=true",
        headers=admin_headers,
        json={"task": "做一次开发"},
    )
    assert r.status_code == 200, r.text
    body = r.json()["result"]
    assert body["tool_calls"] == 1
    assert body["output"] == "已按技能完成"
    # 执行轨迹：包含 skill 调用步骤与返回结果步骤（需 include_trace）
    assert any(s["kind"] == "call" and s["name"] == "skill" for s in body.get("steps", []))
    assert any(s["kind"] == "result" for s in body.get("steps", []))

    # 第一轮的工具定义里必须包含 skill 函数
    _, tools = calls[0]
    assert any(t["function"]["name"] == "skill" for t in tools)
    # 第二轮消息里应包含 SKILL.md 指引内容
    messages = calls[1][0]
    tool_msg = [m for m in messages if m["role"] == "tool"]
    assert tool_msg and marker in tool_msg[-1]["content"]


def _parse_sse(text: str) -> list[dict]:
    events = []
    for block in text.split("\n\n"):
        for line in block.splitlines():
            line = line.strip()
            if not line.startswith("data:"):
                continue
            payload = line[5:].strip()
            if not payload or payload == "[DONE]":
                continue
            events.append(json.loads(payload))
    return events


@pytest.mark.asyncio
async def test_runtime_task_stream_events(client, publisher_headers, admin_headers, monkeypatch):
    """stream=true：事件序至少 start → round_start → reasoning → done。"""
    persona = "llm-stream-persona"
    await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona)
    )

    async def fake_stream(messages, tools, *, on_event=None, emit_tokens=True):
        if on_event:
            await on_event({"type": "reasoning", "content": "先想清楚"})
            await on_event({"type": "token", "content": "流式回答"})
        return {"role": "assistant", "content": "流式回答"}

    monkeypatch.setattr(agent_runner, "is_llm_configured", lambda: True)
    monkeypatch.setattr(agent_runner, "_chat_completion_stream", fake_stream)
    monkeypatch.setattr(
        "app.services.router_key.personal_gateway_credentials",
        lambda user: ("http://gw/v1", "sk-test", "model"),
    )

    r = await client.post(
        f"/api/runtime/agents/{persona}/tasks?stream=true&include_trace=true",
        headers=admin_headers,
        json={"task": "打个招呼"},
    )
    assert r.status_code == 200, r.text
    assert "text/event-stream" in (r.headers.get("content-type") or "")
    events = _parse_sse(r.text)
    types = [e.get("type") for e in events]
    assert "start" in types
    assert "round_start" in types
    assert "reasoning" in types
    assert "done" in types
    assert types.index("start") < types.index("round_start") < types.index("done")
    done = next(e for e in events if e["type"] == "done")
    assert done["result"]["output"] == "流式回答"
    assert done["result"]["mode"] == "llm"
    assert isinstance(done["result"].get("steps"), list)


@pytest.mark.asyncio
async def test_runtime_task_stream_tool_events(
    client, publisher_headers, admin_headers, monkeypatch
):
    """stream=true 且含 tool_calls：出现 tool_start / tool_result。"""
    persona = "llm-stream-tools"
    await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona)
    )

    calls = {"n": 0}

    async def fake_stream(messages, tools, *, on_event=None, emit_tokens=True):
        calls["n"] += 1
        if calls["n"] == 1:
            return {
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
        if on_event:
            await on_event({"type": "token", "content": "工具结果已处理"})
        return {"role": "assistant", "content": "工具结果已处理"}

    monkeypatch.setattr(agent_runner, "is_llm_configured", lambda: True)
    monkeypatch.setattr(agent_runner, "_chat_completion_stream", fake_stream)
    monkeypatch.setattr(
        "app.services.router_key.personal_gateway_credentials",
        lambda user: ("http://gw/v1", "sk-test", "model"),
    )

    r = await client.post(
        f"/api/runtime/agents/{persona}/tasks?stream=true",
        headers=admin_headers,
        json={"task": "调用工具"},
    )
    assert r.status_code == 200, r.text
    events = _parse_sse(r.text)
    types = [e.get("type") for e in events]
    assert "tool_start" in types
    assert "tool_result" in types
    assert types.index("tool_start") < types.index("tool_result")
    start_ev = next(e for e in events if e["type"] == "tool_start")
    assert start_ev["name"] == "不存在的工具"
    done = next(e for e in events if e["type"] == "done")
    assert done["result"]["tool_calls"] == 1
    assert done["result"]["output"] == "工具结果已处理"
    # 默认精简：done 信封无 steps
    assert "steps" not in done["result"]


@pytest.mark.asyncio
async def test_runtime_task_stream_false_still_json(
    client, publisher_headers, admin_headers, monkeypatch
):
    """stream=false（默认）仍返回 200 JSON，默认无 steps。"""
    persona = "llm-stream-sync"
    await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona)
    )

    async def fake_chat(messages, tools):
        return {"choices": [{"message": {"role": "assistant", "content": "同步回答"}}]}

    monkeypatch.setattr(agent_runner, "is_llm_configured", lambda: True)
    monkeypatch.setattr(agent_runner, "_chat_completion", fake_chat)
    monkeypatch.setattr(
        "app.services.router_key.personal_gateway_credentials",
        lambda user: ("http://gw/v1", "sk-test", "model"),
    )

    r = await client.post(
        f"/api/runtime/agents/{persona}/tasks",
        headers=admin_headers,
        json={"task": "打个招呼"},
    )
    assert r.status_code == 200, r.text
    assert "application/json" in (r.headers.get("content-type") or "")
    body = r.json()["result"]
    assert body["output"] == "同步回答"
    assert "steps" not in body
    assert "runtime" not in body


@pytest.mark.asyncio
async def test_chat_completion_stream_parses_sse(monkeypatch):
    """_chat_completion_stream 解析网关 SSE，拼装 message 并推 reasoning/token。"""
    lines = [
        'data: {"choices":[{"delta":{"reasoning_content":"思考"}}]}',
        'data: {"choices":[{"delta":{"content":"你"}}]}',
        'data: {"choices":[{"delta":{"content":"好"}}]}',
        "data: [DONE]",
        "",
    ]

    class FakeResp:
        status_code = 200

        async def aiter_lines(self):
            for line in lines:
                yield line

        async def aread(self):
            return b""

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

    class FakeClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, *a, **k):
            return FakeResp()

    monkeypatch.setattr(agent_runner.httpx, "AsyncClient", FakeClient)
    monkeypatch.setattr(
        agent_runner, "_llm_endpoint", lambda: ("http://gw/v1", "sk", "m")
    )

    events = []

    async def on_event(ev):
        events.append(ev)

    msg = await agent_runner._chat_completion_stream(
        [{"role": "user", "content": "hi"}], [], on_event=on_event
    )
    assert msg["content"] == "你好"
    assert "tool_calls" not in msg
    assert any(e["type"] == "reasoning" and e["content"] == "思考" for e in events)
    assert "".join(e["content"] for e in events if e["type"] == "token") == "你好"
