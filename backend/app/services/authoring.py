"""AI 创作助手：用市场配置的 LLM 生成/润色能力草稿内容。

支持四类：
- agent  专家  → PROMPT.md
- skill  技能  → SKILL.md
- tool   工具  → schema.json(tool_schema) + implementation/tool.py
- mcp    连接器 → connection.json + tools.json + implementation/*.py

复用 ``agent_runner`` 的 OpenAI 兼容调用；未配置 LLM 时返回 503，由前端提示去配置。
生成结果只作草稿，落库仍走各编辑器的保存/审核流程。
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any

from fastapi import HTTPException, status

from app.services.agent_runner import _chat_completion, is_llm_configured

logger = logging.getLogger("market.authoring")

_KINDS = {"agent", "skill", "tool", "mcp"}

_SYSTEM: dict[str, str] = {
    "agent": """你是能力市场的「专家(agent)」创作助手，根据需求写出可直接发布的专家人设 PROMPT.md。
只输出一个 JSON 对象，不要任何解释、不要 markdown 代码围栏。全部用中文（name 除外）。

输出结构：
{"prompt": "<PROMPT.md 全文>", "description": "<一句话简介>", "tags": ["标签", ...]}

PROMPT.md 规范：
---
name: <能力名>
description: <一句话简介>
---
# <能力名>
## 角色
<你是谁，为谁服务>
## 职责
<能做什么，列点>
## 工作流程
<分步骤；明确何时调用连接器/工具/技能>
## 输出要求
<格式与示例>
## 边界
<不做什么、风险与需确认的动作>
""",
    "skill": """你是能力市场的「技能(skill)」创作助手，根据需求写出可直接发布的 SKILL.md。
只输出一个 JSON 对象，不要任何解释、不要 markdown 代码围栏。全部用中文（name 除外）。

输出结构：
{"skill_md": "<SKILL.md 全文>", "description": "<一句话简介>", "tags": ["标签", ...]}

SKILL.md 规范：
---
name: <skill-name：小写字母/数字/短横线>
description: <一句话简介>
---
# <标题>
## 何时使用
## 步骤
（分步骤、可执行、可核对）
## 检查清单
## 输出
""",
    "tool": """你是能力市场的「工具(tool)」创作助手，根据需求写出可直接发布的工具实现。
只输出一个 JSON 对象，不要任何解释、不要 markdown 代码围栏。全部用中文（标识符除外）。

输出结构：
{"tool_schema": {"name": "<工具名>", "description": "<简介>", "parameters": {"type": "object", "properties": {...}, "required": [...]}}, "implementation": "<Python 源码>", "description": "<一句话简介>", "tags": ["标签", ...]}

要求：
- tool_schema.parameters 用标准 JSON Schema（OpenAI function calling 风格）。
- implementation 必须定义 `def run(params): ...`，返回可 JSON 序列化的 dict；仅用标准库。
""",
    "mcp": """你是能力市场的「连接器(mcp)」创作助手，根据需求写出可直接发布的连接器。
只输出一个 JSON 对象，不要任何解释、不要 markdown 代码围栏。全部用中文（标识符除外）。

输出结构：
{"connection": {...}, "tools_json": [{"name": "...", "description": "...", "inputSchema": {...}}], "implementations": [{"path": "implementation/server.py", "content": "<Python 源码>"}], "description": "<一句话简介>", "tags": ["标签", ...]}

connection.json：
- stdio：{"transport": "stdio", "command": "python", "args": ["implementation/server.py"], "env": {"KEY": "${KEY}"}}
- 远程：{"transport": "http", "url": "https://..."}（或 "sse"）
tools_json：该连接器暴露的工具清单。
implementations：stdio 时给出 MCP server 的 Python 源码骨架（可运行），path 必须是 implementation/*.py。
""",
}


def _extract_json(text: str) -> dict[str, Any] | None:
    """从模型输出里提取第一个 JSON 对象。

    仅在整段被 ``` 围栏**整体包裹**时才剥离（避免误删字符串内容里的 ``` 代码块，
    如 SKILL.md 模板中常见的代码围栏）；再用 raw_decode 容忍尾随文字。
    """
    if not text:
        return None
    raw = text.strip()
    m = re.match(r"^```(?:json)?\s*(.*?)\s*```$", raw, re.S | re.I)
    if m:
        raw = m.group(1).strip()
    start = raw.find("{")
    if start == -1:
        return None
    try:
        data, _ = json.JSONDecoder().raw_decode(raw[start:])
    except Exception:  # noqa: BLE001
        return None
    return data if isinstance(data, dict) else None


def _user_prompt(name: str, description: str, instruction: str, current: dict[str, Any]) -> str:
    lines: list[str] = []
    if name:
        lines.append(f"能力名：{name}")
    if description:
        lines.append(f"需求描述：{description}")
    if instruction:
        lines.append(f"创作/修改要求：{instruction}")
    if current:
        blob = json.dumps(current, ensure_ascii=False)
        lines.append("现有内容（可参考或在此基础上修改，长度上限 8000 字）：\n" + blob[:8000])
    return "\n\n".join(lines)


async def generate(
    *,
    kind: str,
    name: str = "",
    description: str = "",
    instruction: str = "",
    current: dict[str, Any] | None = None,
) -> dict[str, Any]:
    kind = (kind or "").strip().lower()
    if kind not in _KINDS:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"不支持的 kind：{kind}（可选 {', '.join(sorted(_KINDS))}）",
        )
    if not is_llm_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "市场未配置 LLM（需 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL），无法使用 AI 创作",
        )
    user = _user_prompt(name, description, instruction, current or {})
    if not user.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "请填写需求描述或修改要求")

    messages = [
        {"role": "system", "content": _SYSTEM[kind]},
        {"role": "user", "content": user},
    ]
    resp = await _chat_completion(messages, [])
    try:
        content = resp["choices"][0]["message"]["content"] or ""
    except (KeyError, IndexError, TypeError) as exc:  # noqa: BLE001
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "LLM 返回结构异常") from exc

    data = _extract_json(content)
    if data is None:
        logger.warning("AI 创作返回无法解析为 JSON: %s", content[:300])
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, "AI 返回无法解析为 JSON，请重试或换种描述")
    return {"kind": kind, "fields": data, "raw": content[:8000]}


async def run_job(job_id: str) -> None:
    """后台执行创作任务并回写状态(独立 DB 会话；由路由 asyncio.create_task 触发)。"""
    from datetime import datetime

    from app.database import SessionLocal
    from app.models import AuthoringJob

    async with SessionLocal() as db:
        job = await db.get(AuthoringJob, job_id)
        if job is None:
            return
        job.status = "running"
        await db.commit()

        req = job.request or {}
        try:
            res = await generate(
                kind=str(req.get("kind") or ""),
                name=str(req.get("name") or ""),
                description=str(req.get("description") or ""),
                instruction=str(req.get("instruction") or ""),
                current=req.get("current") or {},
            )
            job.fields = res.get("fields") or {}
            job.raw = res.get("raw") or ""
            job.status = "done"
        except HTTPException as exc:
            job.status = "error"
            job.error = str(exc.detail)
        except Exception as exc:  # noqa: BLE001
            logger.exception("创作任务失败 job=%s", job_id)
            job.status = "error"
            job.error = f"{type(exc).__name__}: {exc}"
        job.updated_at = datetime.utcnow()
        await db.commit()
