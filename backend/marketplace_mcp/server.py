"""marketplace-mcp 服务（stdio 传输，JSON-RPC 2.0）。

将 AI 能力公共市场暴露为 MCP 工具，供模型/IDE **不下载 zip** 线上调用：
    marketplace_search         搜索/浏览能力
    marketplace_use_tool       云端沙箱调用 tool
    marketplace_call_mcp       调用商品 MCP 暴露的 tool
    marketplace_run_agent      云端 Agent 任务
    marketplace_activate_skill 返回 SKILL.md 正文（注入上下文，非远程执行）
    marketplace_discover_mcp   发现 MCP 能力
    marketplace_run_workflow   云端工作流

鉴权：Bearer 支持市场 JWT 或 SSO access_token。
环境变量：
    MARKETPLACE_URL           默认 http://127.0.0.1:8000
    MARKETPLACE_TOKEN         市场登录 JWT（或通用 Bearer）
    MARKETPLACE_SSO_TOKEN     SSO access_token（优先于 MARKETPLACE_TOKEN）

用法：
    set MARKETPLACE_URL=http://127.0.0.1:8093
    set MARKETPLACE_TOKEN=<token>
    python marketplace_mcp/server.py

接入 Claude Code：
    claude mcp add marketplace -- python E:/ai/market/backend/marketplace_mcp/server.py
"""

from __future__ import annotations

import json
import os
import sys
from urllib.parse import quote

import httpx

MARKETPLACE_URL = os.environ.get("MARKETPLACE_URL", "http://127.0.0.1:8000").rstrip("/")
# SSO 优先：Dashboard / 统一身份签发的 access_token；否则市场本地 JWT
TOKEN = (
    os.environ.get("MARKETPLACE_SSO_TOKEN", "").strip()
    or os.environ.get("MARKETPLACE_TOKEN", "").strip()
)

PROTOCOL_VERSION = "2025-06-18"
SERVER_VERSION = "0.2.0"


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _api(method: str, path: str, **kwargs):
    """调用市场 API；401/403 时透出鉴权与「我的能力」门禁信息。"""
    timeout = kwargs.pop("timeout", 60)
    resp = httpx.request(
        method,
        f"{MARKETPLACE_URL}{path}",
        headers=_headers(),
        timeout=timeout,
        **kwargs,
    )
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except Exception:
            detail = resp.text[:200]
        if resp.status_code in (401, 403):
            hint = (
                "请检查 MARKETPLACE_SSO_TOKEN / MARKETPLACE_TOKEN（市场 JWT 或 SSO Bearer）；"
                "调用执行类接口还需已加入「我的能力」、为作者，或角色在 RUNTIME_ACCESS_ROLES 中。"
            )
            raise RuntimeError(f"市场 API {resp.status_code}: {detail}。{hint}")
        raise RuntimeError(f"市场 API {resp.status_code}: {detail}")
    if resp.status_code == 204 or not resp.content:
        return {}
    return resp.json()


def _text(value: str) -> dict:
    return {"content": [{"type": "text", "text": value}]}


TOOLS = [
    {
        "name": "marketplace_search",
        "description": "搜索 AI 能力公共市场中的能力（Agent/工具/技能/MCP），返回名称、类型、版本、状态、描述。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "q": {"type": "string", "description": "搜索关键词（名称/描述/标签）"},
                "type": {
                    "type": "string",
                    "enum": [
                        "agent",
                        "tool",
                        "skill",
                        "mcp",
                        "plugin",
                        "rule",
                        "command",
                        "hook",
                        "workflow",
                    ],
                    "description": "能力类型",
                },
                "category": {"type": "string", "description": "分类"},
                "limit": {"type": "integer", "description": "返回条数，默认 10", "default": 10},
            },
        },
    },
    {
        "name": "marketplace_use_tool",
        "description": "线上调用市场 tool（云端沙箱，无需下载 zip）。调用前须已加入该能力。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工具名称，如 文件哈希计算"},
                "params": {"type": "object", "description": "工具参数（按工具 schema）", "default": {}},
            },
            "required": ["name"],
        },
    },
    {
        "name": "marketplace_call_mcp",
        "description": (
            "线上调用市场上架的 MCP 能力所暴露的某个 tool（市场侧连接/代理，无需本机装包）。"
            "可先 marketplace_discover_mcp 或 runtime connect 了解可用工具名。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "MCP 能力名称"},
                "tool": {"type": "string", "description": "MCP 暴露的工具名"},
                "params": {"type": "object", "description": "工具参数", "default": {}},
            },
            "required": ["name", "tool"],
        },
    },
    {
        "name": "marketplace_run_agent",
        "description": "线上委派市场上的 Agent 执行任务（云端 runtime，无需下载 zip）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "Agent 名称，如 数字中台分析师"},
                "task": {"type": "string", "description": "委派的任务描述"},
            },
            "required": ["name", "task"],
        },
    },
    {
        "name": "marketplace_activate_skill",
        "description": (
            "按需获取市场技能的 SKILL.md 正文，注入当前对话上下文后按说明执行。"
            "市场只下发文本，不在服务端执行技能。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "技能名称，如 TDD 开发工作流"},
                "context": {"type": "string", "description": "任务上下文，如 写测试"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "marketplace_discover_mcp",
        "description": "动态发现市场上已发布的 MCP 能力（数据库连接、DevOps 工具等）。",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "marketplace_run_workflow",
        "description": "执行市场上已发布的工作流（多步编排：工具/Agent/MCP/技能），输入参数按工作流定义。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "工作流名称"},
                "input": {
                    "type": "object",
                    "description": "工作流入参，如 {\"pattern\": \"**/*.py\"}",
                    "default": {},
                },
            },
            "required": ["name"],
        },
    },
]


def _handle_tool(name: str, arguments: dict) -> dict:
    if name == "marketplace_search":
        limit = int(arguments.get("limit", 10))
        params = {k: v for k, v in arguments.items() if k in ("q", "type", "category") and v}
        params["page_size"] = limit
        body = _api("GET", "/api/capabilities", params=params)
        items = body.get("items") or []
        if not items:
            return _text("没有找到匹配的能力。")
        lines = [f"市场能力搜索结果（共 {body.get('total', 0)} 条，显示 {len(items)} 条）："]
        for c in items:
            lines.append(
                f"- [{c['type']}] {c['name']} v{c['version']} ({c['status']})"
                f" 使用量 {c['usage_count']}，评分 {c['avg_rating']}"
                f"\n  {c.get('description') or ''}"
            )
        return _text("\n".join(lines))

    if name == "marketplace_use_tool":
        tool_name = arguments.get("name")
        params = arguments.get("params") or {}
        result = _api(
            "POST",
            f"/api/runtime/tools/{quote(tool_name, safe='')}/invoke",
            json={"params": params},
        )
        body = result.get("result") or {}
        if body.get("status") != "ok":
            return _text(f"工具执行失败：{body.get('error')}")
        return _text(
            json.dumps(
                {"execution": body.get("execution"), "output": body.get("output")},
                ensure_ascii=False,
                indent=2,
            )
        )

    if name == "marketplace_call_mcp":
        mcp_name = arguments.get("name")
        tool = arguments.get("tool")
        params = arguments.get("params") or {}
        result = _api(
            "POST",
            f"/api/runtime/mcp/{quote(mcp_name, safe='')}/call",
            json={"tool": tool, "params": params},
            timeout=120,
        )
        return _text(json.dumps(result, ensure_ascii=False, indent=2))

    if name == "marketplace_run_agent":
        agent_name = arguments.get("name")
        task = arguments.get("task", "")
        result = _api(
            "POST",
            f"/api/runtime/agents/{quote(agent_name, safe='')}/tasks",
            json={"task": task},
            timeout=180,
        )
        return _text(f"[mode: {result.get('mode')}] {result.get('output') or ''}")

    if name == "marketplace_activate_skill":
        skill_name = arguments.get("name")
        result = _api(
            "POST",
            f"/api/runtime/skills/{quote(skill_name, safe='')}/activate",
            json={"context": arguments.get("context", "")},
        )
        body = result.get("result") or {}
        skill_md = body.get("skill_md") or ""
        cap_name = (result.get("capability") or {}).get("name") or body.get("skill") or skill_name
        header = (
            f"技能「{cap_name}」SKILL.md（请按以下说明执行；"
            f"上下文：{body.get('context') or '（无）'}）\n\n"
        )
        if not skill_md:
            return _text(header + (body.get("note") or "技能包缺少 SKILL.md。"))
        return _text(header + skill_md)

    if name == "marketplace_discover_mcp":
        result = _api("GET", "/api/runtime/mcp/discover")
        tools = result.get("discovered") or []
        if not tools:
            return _text("市场上暂无可发现的 MCP 能力。")
        lines = ["可用的 MCP 能力："]
        for t in tools:
            lines.append(
                f"- {t['name']} v{t['version']}（{t.get('category')}）"
                f"使用量 {t.get('usage_count')}\n  {t.get('description') or ''}"
            )
        return _text("\n".join(lines))

    if name == "marketplace_run_workflow":
        wf_name = arguments.get("name")
        input_data = arguments.get("input") or {}
        result = _api(
            "POST",
            f"/api/runtime/workflows/{quote(wf_name, safe='')}/executions",
            json={"input": input_data},
            timeout=300,
        )
        return _text(
            json.dumps(
                {
                    "state": result.get("state"),
                    "outputs": result.get("outputs"),
                    "node_states": result.get("node_states"),
                    "error": result.get("error"),
                },
                ensure_ascii=False,
                indent=2,
            )
        )

    raise RuntimeError(f"未知工具：{name}")


def main() -> int:
    # 协议传输统一使用 UTF-8，避免 Windows 控制台编码导致 JSON 损坏
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    if not TOKEN:
        sys.stderr.write(
            "[marketplace-mcp] 未设置 MARKETPLACE_SSO_TOKEN 或 MARKETPLACE_TOKEN，"
            "执行类调用将 401。请配置市场 JWT 或 SSO access_token。\n"
        )
    else:
        try:
            me = _api("GET", "/api/auth/me")
            sys.stderr.write(
                f"[marketplace-mcp] 已认证 user={me.get('username') or me.get('id')} "
                f"role={me.get('role')} url={MARKETPLACE_URL}\n"
            )
        except Exception as exc:
            sys.stderr.write(
                f"[marketplace-mcp] 认证失败，请检查 MARKETPLACE_SSO_TOKEN / MARKETPLACE_TOKEN：{exc}\n"
            )
            return 1

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            continue

        method = msg.get("method")
        msg_id = msg.get("id")

        if method == "initialize":
            response = {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "marketplace-mcp", "version": SERVER_VERSION},
                },
            }
            print(json.dumps(response, ensure_ascii=False), flush=True)
            continue

        if method == "notifications/initialized" or method == "notifications/cancelled":
            continue

        if method == "ping":
            print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": {}}), flush=True)
            continue

        if method == "tools/list":
            print(
                json.dumps(
                    {"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}},
                    ensure_ascii=False,
                ),
                flush=True,
            )
            continue

        if method == "tools/call":
            params = msg.get("params") or {}
            name = params.get("name", "")
            arguments = params.get("arguments") or {}
            try:
                result = _handle_tool(name, arguments)
                result["isError"] = False
            except Exception as exc:
                result = _text(f"调用失败：{exc}")
                result["isError"] = True
            print(
                json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}, ensure_ascii=False),
                flush=True,
            )
            continue

        # 未知方法：返回方法不存在
        print(
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": msg_id,
                    "error": {"code": -32601, "message": f"Method not found: {method}"},
                }
            ),
            flush=True,
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
