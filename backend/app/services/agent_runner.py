"""Agent 真实执行器：加载人设 PROMPT + 绑定能力 → LLM 工具调用循环，工具走市场沙箱真实执行。

未配置 LLM 时降级为模拟执行（响应 mode=simulated）；配置 LLM_BASE_URL/LLM_API_KEY/LLM_MODEL
后 mode=llm，即为真实使用。
"""

import json
import logging
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Capability, User
from app.services.agent_editor import read_prompt_deps
from app.services.marketplace import (
    _resolve_manifest,
    invoke_tool,
    record_usage,
    resolve_capability,
)

logger = logging.getLogger("market.agent_runner")

DEFAULT_MAX_ITERATIONS = 10


def is_llm_configured() -> bool:
    s = get_settings()
    return bool(s.llm_base_url and s.llm_api_key and s.llm_model)


async def _chat_completion(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict:
    """调用 OpenAI 兼容 chat/completions（非流式）。"""
    s = get_settings()
    payload: dict[str, Any] = {"model": s.llm_model, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{s.llm_base_url.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {s.llm_api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
        )
    if resp.status_code >= 400:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            f"LLM 调用失败 {resp.status_code}: {resp.text[:300]}",
        )
    return resp.json()


def _tool_defs(runtime: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    defs = []
    for t in runtime.get("tools") or []:
        schema = t.get("schema") or {"type": "object", "properties": {}}
        defs.append(
            {
                "type": "function",
                "function": {
                    "name": t["name"],
                    "description": f"市场工具 {t['name']} v{t.get('version', '')}",
                    "parameters": schema,
                },
            }
        )
    return defs


def _system_prompt(cap: Capability, prompt: str, runtime: dict) -> str:
    system = prompt.strip() or f"你是{cap.name}，请完成用户任务。"
    skill_lines = [
        f"- {s['name']}（v{s.get('version', '')}）：激活后按 SKILL.md 定义执行"
        for s in runtime.get("skills") or []
    ]
    mcp_lines = [
        f"- {m['name']}（v{m.get('version', '')}）：MCP 连接，按 connection.json 使用"
        for m in runtime.get("mcps") or []
    ]
    extra = []
    if skill_lines:
        extra.append("已绑定技能：\n" + "\n".join(skill_lines))
    if mcp_lines:
        extra.append("已绑定 MCP：\n" + "\n".join(mcp_lines))
    if extra:
        system += "\n\n" + "\n\n".join(extra)
    return system


async def run_agent(
    db: AsyncSession,
    user: User,
    cap: Capability,
    task_text: str,
    *,
    max_iterations: int | None = None,
) -> dict[str, Any]:
    """真实执行 agent：加载人设与绑定能力，LLM 工具调用循环；未配置 LLM 时模拟。"""
    if not is_llm_configured():
        return {
            "mode": "simulated",
            "output": (
                f"任务已由 Agent「{cap.name}」v{cap.version} 处理完成（模拟执行）。\n"
                f"收到的任务内容：{task_text}\n"
                f"[角色定义] {cap.description or cap.name}\n"
                "[提示] 配置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 后启用真实执行。"
            ),
            "runtime": {"tools": [], "skills": [], "mcps": []},
            "tool_calls": 0,
        }

    prompt, deps = read_prompt_deps(cap)
    runtime = await _resolve_manifest(db, user, deps or [])
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": _system_prompt(cap, prompt, runtime)},
        {"role": "user", "content": task_text},
    ]
    tools = _tool_defs(runtime)
    iterations = max_iterations or get_settings().agent_max_iterations or DEFAULT_MAX_ITERATIONS
    tool_calls = 0

    for _ in range(iterations):
        data = await _chat_completion(messages, tools)
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        tool_calls_msg = message.get("tool_calls")
        if tool_calls_msg:
            messages.append(
                {
                    "role": "assistant",
                    "content": message.get("content") or "",
                    "tool_calls": tool_calls_msg,
                }
            )
            for tc in tool_calls_msg:
                fn = tc.get("function") or {}
                tool_name = fn.get("name", "")
                try:
                    args = json.loads(fn.get("arguments") or "{}")
                except ValueError:
                    args = {}
                try:
                    tool_cap = await resolve_capability(db, user, tool_name)
                    result = await invoke_tool(db, user, tool_cap, args)
                    content = json.dumps(result, ensure_ascii=False)
                except HTTPException as exc:
                    content = json.dumps({"error": str(exc.detail)}, ensure_ascii=False)
                messages.append(
                    {"role": "tool", "tool_call_id": tc.get("id", ""), "content": content}
                )
                tool_calls += 1
            continue

        output = message.get("content") or ""
        await record_usage(
            db, user, cap, "agent_run", {"task": task_text[:500], "tool_calls": tool_calls}
        )
        await db.commit()
        return {"mode": "llm", "output": output, "runtime": runtime, "tool_calls": tool_calls}

    await record_usage(
        db,
        user,
        cap,
        "agent_run",
        {"task": task_text[:500], "tool_calls": tool_calls, "truncated": True},
    )
    await db.commit()
    return {
        "mode": "llm",
        "output": f"（达到最大迭代次数 {iterations}，任务未完成）",
        "runtime": runtime,
        "tool_calls": tool_calls,
    }
