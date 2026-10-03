"""chatflow 会话模式：会话变量持久化、多轮历史、消息回溯。"""

import pytest

from app.services.workflow_samples import KNOWLEDGE_QA_CHATFLOW, IT_ALERT_WORKFLOW
from app.services.workflows import _canon_type, collect_answers, validate_definition


def _chat_workflow() -> dict:
    return {
        "name": "chat-demo",
        "description": "会话测试",
        "mode": "chat",
        "conversation": {"history_turns": 10},
        "nodes": [
            {"id": "start", "type": "start", "params": {"fields": ["query"]}},
            {
                "id": "echo",
                "type": "template",
                "params": {"template": "回复:${sys.query} 上次:${conversation.topic}"},
            },
            {
                "id": "assign",
                "type": "variable-assigner",
                "params": {"assignments": {"topic": "${sys.query}"}},
            },
            {"id": "answer", "type": "answer", "params": {"answer": "${echo.text}"}},
        ],
        "edges": [
            {"from": "start", "to": "echo"},
            {"from": "echo", "to": "assign"},
            {"from": "assign", "to": "answer"},
        ],
    }


def test_collect_answers_orders_and_skips_empty():
    nodes = {
        "a": {"id": "a", "type": "answer"},
        "b": {"id": "b", "type": "llm"},
        "c": {"id": "c", "type": "answer"},
    }
    outputs = {"a": {"answer": "第一"}, "c": {"answer": ""}, "b": {"text": "x"}}
    assert collect_answers(nodes, outputs) == "第一"


def test_chatflow_sample_valid():
    order = validate_definition(KNOWLEDGE_QA_CHATFLOW)
    assert set(order) == {"start", "kb", "llm", "assign", "answer"}
    assert _canon_type(KNOWLEDGE_QA_CHATFLOW["nodes"][4]["type"]) == "answer"
    # 旧的可视工作流仍可校验
    assert "end" in validate_definition(IT_ALERT_WORKFLOW)


async def _publish_workflow(client, headers, admin_headers, name: str, workflow: dict) -> None:
    r = await client.post(
        "/api/workflows",
        headers=headers,
        json={
            "name": name,
            "description": "chatflow 测试",
            "version": "1.0.0",
            "category": "能力编排",
            "tags": [],
            "workflow": workflow,
        },
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(f"/api/publish/capabilities/{cap_id}/submit", headers=headers)
    assert r.status_code == 200, r.text
    r = await client.post(
        f"/api/admin/capabilities/{cap_id}/review",
        headers=admin_headers,
        json={"action": "approve", "comment": "ok"},
    )
    assert r.status_code == 200, r.text


@pytest.mark.asyncio
async def test_chatflow_multi_turn_persists_conversation(
    client, publisher_headers, admin_headers
):
    name = "chat-demo"
    await _publish_workflow(client, publisher_headers, admin_headers, name, _chat_workflow())

    # 第一轮：新建会话
    r = await client.post(
        f"/api/runtime/workflows/{name}/chat",
        headers=admin_headers,
        json={"query": "你好"},
    )
    assert r.status_code == 200, r.text
    first = r.json()
    assert first["state"] == "succeeded", first
    assert first["answer"] == "回复:你好 上次:"
    assert first["variables"] == {"topic": "你好"}
    conv_id = first["conversation_id"]
    assert conv_id

    # 第二轮：带 conversation_id，注入会话变量 + 历史
    r = await client.post(
        f"/api/runtime/workflows/{name}/chat",
        headers=admin_headers,
        json={"query": "再见", "conversation_id": conv_id},
    )
    assert r.status_code == 200, r.text
    second = r.json()
    assert second["conversation_id"] == conv_id
    assert second["answer"] == "回复:再见 上次:你好"
    assert second["variables"] == {"topic": "再见"}

    # 会话列表
    r = await client.get(
        f"/api/runtime/workflows/{name}/conversations", headers=admin_headers
    )
    assert r.status_code == 200, r.text
    convs = r.json()
    assert len(convs) == 1
    assert convs[0]["id"] == conv_id
    assert convs[0]["workflow_name"] == name
    assert convs[0]["title"] == "你好"

    # 消息回溯：两轮 user/assistant
    r = await client.get(
        f"/api/runtime/workflows/conversations/{conv_id}/messages",
        headers=admin_headers,
    )
    assert r.status_code == 200, r.text
    msgs = r.json()
    assert [m["role"] for m in msgs] == ["user", "assistant", "user", "assistant"]
    assert msgs[0]["content"] == "你好"
    assert msgs[3]["content"] == "回复:再见 上次:你好"

    # 删除会话
    r = await client.delete(
        f"/api/runtime/workflows/conversations/{conv_id}", headers=admin_headers
    )
    assert r.status_code == 200, r.text
    r = await client.get(
        f"/api/runtime/workflows/conversations/{conv_id}/messages",
        headers=admin_headers,
    )
    assert r.status_code == 404


@pytest.mark.asyncio
async def test_chatflow_unknown_conversation_404(
    client, publisher_headers, admin_headers
):
    name = "chat-demo-2"
    await _publish_workflow(client, publisher_headers, admin_headers, name, _chat_workflow())
    r = await client.post(
        f"/api/runtime/workflows/{name}/chat",
        headers=admin_headers,
        json={"query": "hi", "conversation_id": "nonexistent-id"},
    )
    assert r.status_code == 404
