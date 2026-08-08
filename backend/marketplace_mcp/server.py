"""marketplace-mcp 服务（stdio 传输，JSON-RPC 2.0，零第三方依赖）。

将 AI 能力公共市场的核心能力暴露为 MCP 工具：
    marketplace_search        搜索/浏览能力
    marketplace_use_tool      真实调用工具（沙箱执行）
    marketplace_run_agent     实例化 Agent（可改用 A2A 委派）
    marketplace_activate_skill 激活技能
    marketplace_discover_mcp   动态发现 MCP 能力

用法：
    set MARKETPLACE_URL=http://127.0.0.1:8000
    set MARKETPLACE_TOKEN=<登录后获取的 Bearer Token>
    python marketplace_mcp/server.py

接入 Claude Code：
    claude mcp add marketplace -- python E:/ai/market/backend/marketplace_mcp/server.py
接入 Codex / Cursor：在 MCP 配置里指向同样命令即可。
"""

import json
import os
import sys
from urllib.parse import quote

import httpx

MARKETPLACE_URL = os.environ.get("MARKETPLACE_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = os.environ.get("MARKETPLACE_TOKEN", "")

PROTOCOL_VERSION = "2025-06-18"


def _headers() -> dict[str, str]:
    h = {"Content-Type": "application/json"}
    if TOKEN:
        h["Authorization"] = f"Bearer {TOKEN}"
    return h


def _api(method: str, path: str, **kwargs):
    resp = httpx.request(method, f"{MARKETPLACE_URL}{path}", headers=_headers(), timeout=30, **kwargs)
    if resp.status_code >= 400:
        try:
            detail = resp.json().get("detail")
        except Exception:
            detail = resp.text[:200]
        raise RuntimeError(f"市场 API {resp.status_code}: {detail}")
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
                "type": {"type": "string", "enum": ["agent", "tool", "skill", "mcp"], "description": "能力类型"},
                "category": {"type": "string", "description": "分类"},
                "limit": {"type": "integer", "description": "返回条数，默认 10", "default": 10},
            },
        },
    },
    {
        "name": "marketplace_use_tool",
        "description": "调用市场上已发布的工具（真实沙箱执行）。示例：计算文件哈希、生成报表数据等。",
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
        "name": "marketplace_run_agent",
        "description": "实例化市场上的 Agent 角色并交给它一个任务（委派执行）。示例：数字中台分析师 生成上月销售报表。",
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
        "description": "激活市场中的技能（可复用工作流模板），当前调用方按 SKILL.md 定义执行。",
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
]


def _handle_tool(name: str, arguments: dict) -> dict:
    if name == "marketplace_search":
        limit = int(arguments.get("limit", 10))
        params = {k: v for k, v in arguments.items() if k in ("q", "type", "category") and v}
        params["page_size"] = limit
        items = _api("GET", "/api/capabilities", params=params)
        if not items:
            return _text("没有找到匹配的能力。")
        lines = ["市场能力搜索结果："]
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
            f"/api/runtime/tools/{quote(tool_name)}/invoke",
            json={"params": params},
        )
        body = result.get("result") or {}
        if body.get("status") != "ok":
            return _text(f"工具执行失败：{body.get('error')}")
        return _text(json.dumps({"execution": body.get("execution"), "output": body.get("output")}, ensure_ascii=False, indent=2))

    if name == "marketplace_run_agent":
        agent_name = arguments.get("name")
        task = arguments.get("task", "")
        result = _api(
            "POST",
            f"/api/runtime/agents/{quote(agent_name)}/instances",
            params={"task": task},
        )
        return _text(json.dumps(result.get("result") or result, ensure_ascii=False, indent=2))

    if name == "marketplace_activate_skill":
        skill_name = arguments.get("name")
        result = _api(
            "POST",
            f"/api/runtime/skills/{quote(skill_name)}/activate",
            json={"context": arguments.get("context", "")},
        )
        return _text(f"技能「{result.get('capability', {}).get('name')}」已激活，请按以下说明执行任务：\n{result.get('capability', {}).get('description', '')}")

    if name == "marketplace_discover_mcp":
        result = _api("GET", "/api/runtime/mcp/discover")
        tools = result.get("discovered") or []
        if not tools:
            return _text("市场上暂无可发现的 MCP 能力。")
        lines = ["可用的 MCP 能力："]
        for t in tools:
            lines.append(f"- {t['name']} v{t['version']}（{t.get('category')}）使用量 {t.get('usage_count')}\n  {t.get('description') or ''}")
        return _text("\n".join(lines))

    raise RuntimeError(f"未知工具：{name}")


def main() -> int:
    # 协议传输统一使用 UTF-8，避免 Windows 控制台编码导致 JSON 损坏
    if hasattr(sys.stdin, "reconfigure"):
        sys.stdin.reconfigure(encoding="utf-8")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")

    if TOKEN:
        try:
            _api("GET", "/api/auth/me")
        except Exception as exc:
            sys.stderr.write(f"[marketplace-mcp] 认证失败，请检查 MARKETPLACE_TOKEN：{exc}\n")
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
                    "serverInfo": {"name": "marketplace-mcp", "version": "0.1.0"},
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
            print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": {"tools": TOOLS}}, ensure_ascii=False), flush=True)
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
            print(json.dumps({"jsonrpc": "2.0", "id": msg_id, "result": result}, ensure_ascii=False), flush=True)
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
