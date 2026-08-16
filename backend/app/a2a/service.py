"""A2A 服务：Agent Card 构建、任务执行与持久化。"""

import uuid
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload, selectinload

from app.a2a.protocol import (
    A2AArtifact,
    A2AMessage,
    A2APart,
    A2AStatus,
    A2ATask,
    AgentCard,
    AgentCapabilities,
    AgentSkill,
    SecurityScheme,
    extract_text,
)
from app.config import get_settings
from app.models import A2ATask as A2ATaskModel
from app.models import Capability, UsageEvent, User
from app.services.capabilities import get_visible_capabilities, parse_semver
from app.services.marketplace import record_usage

TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}


def build_agent_card(
    cap: Capability,
    *,
    base_url: str,
    meta: bool = False,
    skills: list[AgentSkill] | None = None,
) -> AgentCard:
    """构建 A2A Agent Card。meta=True 时生成市场本身的元卡片（聚合所有 Agent 技能）。"""
    if meta:
        return AgentCard(
            name=get_settings().app_name,
            description="聚合平台内所有已发布 Agent 能力的统一入口：发现 Agent Card 并委派任务。",
            url=f"{base_url}/api/a2a",
            version=get_settings().app_version,
            provider={"organization": cap.organization or "平台部"},
            documentation_url=f"{base_url}/docs",
            capabilities=AgentCapabilities(state_transition_history=True),
            security_schemes={"bearer": SecurityScheme()},
            security=[{"scheme": "bearer"}],
            skills=skills or [],
        )

    return AgentCard(
        name=cap.name,
        description=cap.description or f"Agent {cap.name}",
        # A2A 规范：card.url 是 Agent 的 JSON-RPC 任务端点（tasks/send 等 POST 目标）
        url=f"{base_url}/api/a2a/agents/{cap.id}/a2a",
        version=cap.version,
        provider={"organization": cap.organization or "平台部", "author": cap.author.username if cap.author else ""},
        documentation_url=f"{base_url}/api/capabilities/{cap.id}",
        capabilities=AgentCapabilities(state_transition_history=True),
        security_schemes={"bearer": SecurityScheme()},
        security=[{"scheme": "bearer"}],
        skills=[
            AgentSkill(
                id=cap.id,
                name=cap.name,
                description=cap.description or "",
                tags=cap.tags or [],
                examples=["委派任务给该 Agent 并等待结果"],
            )
        ],
    )


async def get_a2a_agents(db: AsyncSession, user: User | None) -> list[Capability]:
    """返回启用了 A2A 的 Agent 能力（已发布/弃用且当前可见）。"""
    visible = await get_visible_capabilities(db, user)
    agents = [
        c
        for c in visible
        if c.type == "agent" and c.status in ("published", "deprecated")
    ]
    agents.sort(key=lambda c: (c.name, parse_semver(c.version)), reverse=True)
    return agents


async def resolve_a2a_agent(
    db: AsyncSession, user: User | None, cap_id: str
) -> Capability:
    agents = await get_a2a_agents(db, user)
    agent = next((c for c in agents if c.id == cap_id), None)
    if agent is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            "Agent 不存在或未启用 A2A（仅已发布的 Agent 类型能力可互调）",
        )
    return agent


async def send_task(
    db: AsyncSession,
    user: User,
    agent: Capability,
    *,
    client_task_id: str,
    message: dict[str, Any],
    metadata: dict[str, Any] | None = None,
) -> A2ATaskModel:
    """创建任务并同步执行：LLM 已配置时真实执行（mode=llm），否则模拟（mode=simulated）。"""
    task_id = str(uuid.uuid4())
    task = A2ATaskModel(
        id=task_id,
        agent_id=agent.id,
        state="submitted",
        message=message,
        history=[message],
        task_metadata=metadata or {},
        client_task_id=client_task_id,
        created_by=user.id,
    )
    db.add(task)
    await db.flush()

    task.state = "working"
    input_text = extract_text(message)
    try:
        # 复用执行引擎：实例化 Agent 并记录用量（action=a2a_task）
        await record_usage(
            db,
            user,
            agent,
            "a2a_task",
            {"task_id": task_id, "input": input_text[:500], "client_task_id": client_task_id},
        )
        from app.services.agent_runner import run_agent

        result = await run_agent(db, user, agent, input_text or "（无文本内容）")
        output_text = result.get("output") or ""
        mode = result.get("mode", "simulated")
        output_text = (
            f"{output_text}\n\n[执行模式] {mode}"
            if mode != "simulated"
            else output_text
        )
        output_message = {
            "role": "agent",
            "parts": [{"type": "text", "text": output_text}],
        }
        task.state = "completed"
        task.task_metadata = {**(task.task_metadata or {}), "mode": mode}
        task.output_message = output_message
        task.artifacts = [
            {
                "name": "task_result",
                "description": f"Agent {agent.name} 的执行结果",
                "parts": [{"type": "text", "text": output_text}],
                "index": 0,
            }
        ]
        task.history = [message, output_message]
    except HTTPException as exc:
        task.state = "failed"
        task.output_message = {
            "role": "agent",
            "parts": [{"type": "text", "text": f"执行失败：{exc.detail}"}],
        }
    except Exception as exc:  # pragma: no cover
        task.state = "failed"
        task.output_message = {
            "role": "agent",
            "parts": [{"type": "text", "text": f"执行失败：{exc}"}],
        }

    await db.commit()
    await db.refresh(task)
    return task


async def get_task(db: AsyncSession, user: User, task_id: str) -> A2ATaskModel:
    task = await db.get(A2ATaskModel, task_id)
    if task is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    if user.role != "admin" and task.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权查看该任务")
    return task


async def cancel_task(db: AsyncSession, user: User, task_id: str, reason: str | None = None) -> A2ATaskModel:
    task = await get_task(db, user, task_id)
    if task.state in TERMINAL_STATES:
        return task
    task.state = "canceled"
    task.output_message = {
        "role": "agent",
        "parts": [{"type": "text", "text": f"任务已取消：{reason or '由请求方取消'}"}],
    }
    task.task_metadata = {**(task.task_metadata or {}), "cancel_reason": reason or ""}
    await db.commit()
    await db.refresh(task)
    return task


def to_protocol_task(task: A2ATaskModel) -> A2ATask:
    return A2ATask(
        id=task.id,
        status=A2AStatus(
            state=task.state,  # type: ignore[arg-type]
            message=A2AMessage.model_validate(task.output_message)
            if task.output_message
            else None,
        ),
        artifacts=[A2AArtifact.model_validate(a) for a in (task.artifacts or [])],
        history=[A2AMessage.model_validate(m) for m in (task.history or [])],
        metadata=task.task_metadata or {},
    )
