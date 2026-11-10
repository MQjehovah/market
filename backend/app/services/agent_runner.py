"""Agent 真实执行器：加载人设 PROMPT + 绑定能力 → LLM 工具调用循环，工具走市场沙箱真实执行。

未配置 LLM 时降级为模拟执行（响应 mode=simulated）；配置 LLM_BASE_URL/LLM_API_KEY/LLM_MODEL
后 mode=llm，即为真实使用。
"""

from __future__ import annotations

import contextvars
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.models import Capability, User
from app.services.mcp_bridge import MCPBridge
from app.services.agent_editor import read_prompt_deps
from app.services.mcp_gateway import load_gateway_config_by_name
from app.services.marketplace import (
    _resolve_manifest,
    invoke_tool,
    record_usage,
    resolve_capability,
)

logger = logging.getLogger("market.agent_runner")

DEFAULT_MAX_ITERATIONS = 10

# 试用传入个人网关密钥时，本轮 chat 调用用这组 (base, key, model)，不再用进程级 LLM_API_KEY。
_llm_ctx: contextvars.ContextVar[tuple[str, str, str] | None] = contextvars.ContextVar(
    "llm_ctx", default=None
)
TRACE_CLIP = 3000
# 注入 LLM messages 的 tool content 上限（大于 TRACE_CLIP，避免专家多轮被巨型 MCP 回包拖慢）
MESSAGE_TOOL_CLIP = 6000

EventCallback = Callable[[dict[str, Any]], Awaitable[None]]


def _clip(text: str, limit: int = TRACE_CLIP) -> str:
    if text is None:
        return ""
    text = str(text)
    return text if len(text) <= limit else text[:limit] + f"…（截断，共 {len(text)} 字符）"


async def _emit(on_event: EventCallback | None, event: dict[str, Any]) -> None:
    if on_event is None:
        return
    try:
        await on_event(event)
    except Exception:  # noqa: BLE001
        logger.debug("agent_runner on_event failed", exc_info=True)


def is_llm_configured() -> bool:
    s = get_settings()
    return bool(s.llm_base_url and s.llm_api_key and s.llm_model)


def _llm_endpoint() -> tuple[str, str, str]:
    override = _llm_ctx.get()
    if override:
        return override
    s = get_settings()
    return s.llm_base_url, s.llm_api_key, s.llm_model


async def _chat_completion(messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> dict:
    """调用 OpenAI 兼容 chat/completions（非流式）。"""
    base, api_key, model = _llm_endpoint()
    payload: dict[str, Any] = {"model": model, "messages": messages}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{base.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
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


def _merge_tool_call_delta(acc: dict[int, dict[str, Any]], tc: dict[str, Any]) -> None:
    """把流式 tool_calls delta 合并进按 index 累积的结构。"""
    try:
        idx = int(tc.get("index", 0))
    except (TypeError, ValueError):
        idx = 0
    slot = acc.setdefault(
        idx,
        {"id": "", "type": "function", "function": {"name": "", "arguments": ""}},
    )
    if tc.get("id"):
        slot["id"] = str(tc["id"])
    if tc.get("type"):
        slot["type"] = str(tc["type"])
    fn = tc.get("function") or {}
    if not isinstance(fn, dict):
        return
    if fn.get("name"):
        slot["function"]["name"] = str(fn["name"])
    if fn.get("arguments"):
        slot["function"]["arguments"] = (slot["function"].get("arguments") or "") + str(
            fn["arguments"]
        )


async def _chat_completion_stream(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]],
    *,
    on_event: EventCallback | None = None,
    emit_tokens: bool = True,
) -> dict:
    """流式 chat/completions：边推 reasoning/token，边拼装完整 message。

    返回形如非流式 choice.message 的 dict（含 content / tool_calls）。
    """
    base, api_key, model = _llm_endpoint()
    payload: dict[str, Any] = {"model": model, "messages": messages, "stream": True}
    if tools:
        payload["tools"] = tools
        payload["tool_choice"] = "auto"

    content_parts: list[str] = []
    tool_acc: dict[int, dict[str, Any]] = {}
    saw_tool_calls = False

    async with httpx.AsyncClient(timeout=300) as client:
        async with client.stream(
            "POST",
            f"{base.rstrip('/')}/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
                "Accept": "text/event-stream",
            },
            json=payload,
        ) as resp:
            if resp.status_code >= 400:
                body = (await resp.aread()).decode("utf-8", errors="replace")[:300]
                raise HTTPException(
                    status.HTTP_502_BAD_GATEWAY,
                    f"LLM 调用失败 {resp.status_code}: {body}",
                )
            async for raw_line in resp.aiter_lines():
                line = (raw_line or "").strip()
                if not line:
                    continue
                if line.startswith(":"):
                    continue
                if not line.startswith("data:"):
                    continue
                data_str = line[5:].strip()
                if not data_str or data_str == "[DONE]":
                    continue
                try:
                    chunk = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                choice = (chunk.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                if not isinstance(delta, dict):
                    continue

                reasoning = delta.get("reasoning_content")
                if reasoning is None:
                    reasoning = delta.get("reasoning")
                if isinstance(reasoning, str) and reasoning:
                    await _emit(on_event, {"type": "reasoning", "content": reasoning})

                tcs = delta.get("tool_calls")
                if isinstance(tcs, list) and tcs:
                    saw_tool_calls = True
                    for tc in tcs:
                        if isinstance(tc, dict):
                            _merge_tool_call_delta(tool_acc, tc)

                piece = delta.get("content")
                if isinstance(piece, str) and piece:
                    content_parts.append(piece)
                    # 收束轮才推 token；一旦出现 tool_calls 就不再推正文增量
                    if emit_tokens and not saw_tool_calls:
                        await _emit(on_event, {"type": "token", "content": piece})

    message: dict[str, Any] = {
        "role": "assistant",
        "content": "".join(content_parts),
    }
    if tool_acc:
        message["tool_calls"] = [tool_acc[i] for i in sorted(tool_acc.keys())]
    return message


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


def _embedded_skill_bodies(cap: Capability) -> dict[str, str]:
    """专家包内 skills/<name>/SKILL.md。不必单独上架。"""
    from app.services.mcp_gateway import read_package_files

    bodies: dict[str, str] = {}
    for path, raw in read_package_files(cap).items():
        norm = path.replace("\\", "/").lstrip("/")
        if not norm.startswith("skills/") or not norm.endswith("/SKILL.md"):
            continue
        folder = norm[len("skills/") : -len("/SKILL.md")]
        if not folder or "/" in folder:
            continue
        text = raw.decode("utf-8", errors="replace")
        name = folder
        if text.startswith("---"):
            end = text.find("\n---", 3)
            if end > 0:
                for line in text[3:end].splitlines():
                    if line.startswith("name:"):
                        name = line.split(":", 1)[1].strip().strip("\"'") or folder
                        break
        if text.strip():
            bodies[name] = text
    return bodies


def _skill_tool_def(skills: list[dict[str, Any]]) -> dict[str, Any] | None:
    """skill 工具定义：让 LLM 显式加载已绑定技能的执行指引。"""
    names = [s["name"] for s in skills]
    if not names:
        return None
    return {
        "type": "function",
        "function": {
            "name": "skill",
            "description": (
                "加载并激活一个已绑定的技能，返回该技能的完整执行指引（SKILL.md）。"
                "开始执行任务前，先判断是否有适用于当前任务的技能，如有则先调用本工具。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "skill": {
                        "type": "string",
                        "enum": names,
                        "description": "要激活的技能名称",
                    },
                    "context": {"type": "string", "description": "当前任务上下文（可选）"},
                },
                "required": ["skill"],
            },
        },
    }


async def _activate_skill(
    db: AsyncSession,
    user: User,
    args: dict[str, Any],
    embedded: dict[str, str] | None = None,
) -> str:
    """执行 skill 工具：优先读专家包内的 SKILL.md，没有再查单独上架的技能。"""
    from app.services.marketplace import activate_skill

    name = (args or {}).get("skill") or ""
    if not name:
        return json.dumps({"ok": False, "error": "缺少 skill 参数"}, ensure_ascii=False)
    body = (embedded or {}).get(name)
    if body:
        return json.dumps(
            {"ok": True, "skill": name, "version": "", "skill_md": body},
            ensure_ascii=False,
        )
    try:
        cap = await resolve_capability(db, user, name)
    except HTTPException as exc:
        return json.dumps({"ok": False, "error": str(exc.detail)}, ensure_ascii=False)
    if cap.type != "skill":
        return json.dumps({"ok": False, "error": f"{name} 不是技能能力"}, ensure_ascii=False)
    try:
        result = await activate_skill(db, user, cap, str((args or {}).get("context") or ""))
    except Exception as exc:  # noqa: BLE001
        return json.dumps({"ok": False, "error": f"读取技能失败：{exc}"}, ensure_ascii=False)
    md = result.get("skill_md") or ""
    if not md:
        return json.dumps({"ok": False, "error": "技能包缺少 SKILL.md"}, ensure_ascii=False)
    return json.dumps(
        {
            "ok": True,
            "skill": result.get("skill") or cap.name,
            "version": result.get("version") or cap.version,
            "skill_md": md,
        },
        ensure_ascii=False,
    )


def _system_prompt(cap: Capability, prompt: str, runtime: dict) -> str:
    system = prompt.strip() or f"你是{cap.name}，请完成用户任务。"
    skill_lines = [
        f"- {s['name']}（v{s.get('version', '')}）：使用 skill 工具激活后按 SKILL.md 定义执行"
        for s in runtime.get("skills") or []
    ]
    connected = {
        m.get("name")
        for m in runtime.get("mcp_connected") or []
        if m.get("connected")
    }
    mcp_lines = [
        f"- {m['name']}（v{m.get('version', '')}）：MCP 已连接，其工具已注册为可用函数（mcp_* 前缀）"
        for m in runtime.get("mcps") or []
        if m["name"] in connected
    ]
    failed_lines = [
        f"- {m.get('name')}：未连接（{m.get('error') or '未知原因'}）——其工具本次不可用，"
        "不要尝试调用该连接器的任何工具；涉及的数据按【数据缺口·原因】标注，并向用户说明需要先配置凭据或由管理员排查。"
        for m in runtime.get("mcp_connected") or []
        if not m.get("connected")
    ]
    extra = []
    if skill_lines:
        extra.append("已绑定技能：\n" + "\n".join(skill_lines))
    if mcp_lines:
        extra.append("已绑定 MCP：\n" + "\n".join(mcp_lines))
    if failed_lines:
        extra.append("绑定但本次未连接的 MCP：\n" + "\n".join(failed_lines))
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
    extra_deps: list[dict[str, str]] | None = None,
    credentials: tuple[str, str, str] | None = None,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    """真实执行 agent。credentials 为 (网关根, 密钥, 模型) 时按这组调用。
    都没有时仍返回模拟结果，供 A2A 等旧入口使用。网页试用不走这条。
    on_event 非空时走流式 LLM，并推送 round_start/reasoning/token/tool_*。
    """
    if credentials is None and not is_llm_configured():
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

    ctx_token = _llm_ctx.set(credentials) if credentials else None
    try:
        return await _run_agent_llm(
            db,
            user,
            cap,
            task_text,
            max_iterations=max_iterations,
            extra_deps=extra_deps,
            on_event=on_event,
        )
    finally:
        if ctx_token is not None:
            _llm_ctx.reset(ctx_token)


async def _run_agent_llm(
    db: AsyncSession,
    user: User,
    cap: Capability,
    task_text: str,
    *,
    max_iterations: int | None = None,
    extra_deps: list[dict[str, str]] | None = None,
    on_event: EventCallback | None = None,
) -> dict[str, Any]:
    prompt, deps = read_prompt_deps(cap)
    runtime = await _resolve_manifest(db, user, deps or [])
    # 工作流 agent 节点内联绑定的额外能力（并入专家包依赖）
    if extra_deps:
        extra = await _resolve_manifest(db, user, extra_deps)
        for bucket in ("tools", "skills", "mcps"):
            seen = {x.get("name") for x in runtime.get(bucket) or []}
            for item in extra.get(bucket) or []:
                if item.get("name") not in seen:
                    runtime.setdefault(bucket, []).append(item)

    embedded = _embedded_skill_bodies(cap)
    seen_skills = {item.get("name") for item in runtime.get("skills") or []}
    for skill_name in embedded:
        if skill_name not in seen_skills:
            runtime.setdefault("skills", []).append(
                {"name": skill_name, "version": cap.version, "embedded": True}
            )

    async def _gateway_loader(name: str):
        try:
            return await load_gateway_config_by_name(db, name)
        except Exception:  # noqa: BLE001
            return None

    bridge = MCPBridge(gateway_loader=_gateway_loader)
    from app.services.capability_secrets import (
        is_service_identity,
        is_user_bound,
        is_user_only,
        resolve_capability_env,
        resolve_user_bound_env_for,
    )

    try:
        mcp_info: list[dict[str, Any]] = []
        for m in runtime.get("mcps") or []:
            try:
                mcp_cap = await resolve_capability(db, user, m["name"])
                # 平台轨：注入该能力名的平台密钥（与调用者身份无关）；user 级能力
                # 按身份取执行凭据（服务令牌→平台；用户令牌→个人凭据 fail-closed；
                # user_only 平台身份一律拒绝）
                cap_env = await resolve_capability_env(db, mcp_cap.name)
                user_env: dict[str, str] = {}
                if is_user_bound(mcp_cap):
                    if is_user_only(mcp_cap) and is_service_identity(user):
                        mcp_info.append(
                            {
                                "name": m["name"],
                                "connected": False,
                                "error": "该能力仅支持按用户身份执行（不提供平台兜底）",
                            }
                        )
                        continue
                    user_env, missing = await resolve_user_bound_env_for(
                        db, mcp_cap, user, platform_env=cap_env
                    )
                    if missing:
                        mcp_info.append(
                            {
                                "name": m["name"],
                                "connected": False,
                                "error": "按用户身份执行，缺少必需凭据："
                                + "、".join(missing),
                            }
                        )
                        continue
                mcp_info.append(
                    await bridge.connect_capability(m["name"], mcp_cap, env=cap_env, user_env=user_env)
                )
            except Exception as exc:  # noqa: BLE001
                mcp_info.append({"name": m["name"], "connected": False, "error": str(exc)[:200]})
        runtime["mcp_connected"] = mcp_info
        runtime["mcp_tools"] = bridge.tool_names

        messages: list[dict[str, Any]] = [
            {"role": "system", "content": _system_prompt(cap, prompt, runtime)},
            {"role": "user", "content": task_text},
        ]
        tools = _tool_defs(runtime) + bridge.tool_defs
        skill_def = _skill_tool_def(runtime.get("skills") or [])
        if skill_def:
            tools.append(skill_def)
        from app.services.runtime_settings import agent_max_iterations as _runtime_max_iters

        iterations = max_iterations or _runtime_max_iters() or DEFAULT_MAX_ITERATIONS
        tool_calls = 0
        steps: list[dict[str, Any]] = []
        stream_mode = on_event is not None

        for round_idx in range(1, iterations + 1):
            await _emit(on_event, {"type": "round_start", "round": round_idx})
            if stream_mode:
                message = await _chat_completion_stream(
                    messages, tools, on_event=on_event, emit_tokens=True
                )
            else:
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
                    args_clip = _clip(json.dumps(args, ensure_ascii=False))
                    steps.append(
                        {
                            "kind": "call",
                            "name": tool_name,
                            "arguments": args_clip,
                            "reasoning": _clip(message.get("content") or ""),
                        }
                    )
                    await _emit(
                        on_event,
                        {"type": "tool_start", "name": tool_name, "arguments": args_clip},
                    )
                    try:
                        if tool_name == "skill":
                            content = await _activate_skill(db, user, args, embedded)
                        elif bridge.has_tool(tool_name):
                            content = await bridge.call(tool_name, args)
                        else:
                            try:
                                tool_cap = await resolve_capability(db, user, tool_name)
                            except HTTPException as exc:
                                if exc.status_code == 404:
                                    names = ", ".join(sorted(bridge.tool_names)[:30]) or "（无）"
                                    raise HTTPException(
                                        exc.status_code,
                                        f"工具 {tool_name} 不存在；可用工具：{names}",
                                    ) from exc
                                raise
                            result = await invoke_tool(db, user, tool_cap, args)
                            content = json.dumps(result, ensure_ascii=False)
                    except HTTPException as exc:
                        content = json.dumps({"error": str(exc.detail)}, ensure_ascii=False)
                    # steps 用短 clip；写入对话的 content 单独限长，防止后续回合上下文膨胀
                    result_clip = _clip(content)
                    steps.append({"kind": "result", "name": tool_name, "result": result_clip})
                    await _emit(
                        on_event,
                        {"type": "tool_result", "name": tool_name, "result": result_clip},
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id", ""),
                            "content": _clip(content, MESSAGE_TOOL_CLIP),
                        }
                    )
                    tool_calls += 1
                continue

            output = message.get("content") or ""
            steps.append({"kind": "output", "output": _clip(output)})
            await record_usage(
                db, user, cap, "agent_run", {"task": task_text[:500], "tool_calls": tool_calls}
            )
            await db.commit()
            return {
                "mode": "llm",
                "output": output,
                "runtime": runtime,
                "tool_calls": tool_calls,
                "steps": steps,
            }

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
            "steps": steps,
        }
    finally:
        await bridge.close()
