"""能力市场货架与 kind 语义（控制面目录，不是六个并列商店）。

type 字段 = kind；门户按 shelf（组件 / 助手 / 安装包）组织。
"""

from __future__ import annotations

from typing import Any

SHELVES: dict[str, dict[str, Any]] = {
    "brick": {
        "key": "brick",
        "label": "组件",
        "description": "技能 / 连接器 / 编排函数（rule/command/hook 保留能力、逛店默认不展示）。",
        "kinds": ["skill", "mcp", "tool", "rule", "command", "hook"],
    },
    "recipe": {
        "key": "recipe",
        "label": "助手",
        "description": "助手（Agent，可含 TEAM.md）与能力编排平行。",
        "kinds": ["agent", "workflow"],
    },
    "install": {
        "key": "install",
        "label": "安装包",
        "description": "一次分发技能 + 连接器（可选助手）。能力保留，逛店默认不展示。",
        "kinds": ["plugin"],
    },
}

KIND_SHELF: dict[str, str] = {
    kind: shelf_key
    for shelf_key, shelf in SHELVES.items()
    for kind in shelf["kinds"]
}

# 浏览默认：助手 + 依赖（技能 / 连接器）；安装包与 rule/command/hook 保留能力但不逛店展示
DEFAULT_BROWSE_KINDS = ("agent", "skill", "mcp")
MORE_BROWSE_KINDS = ("workflow", "tool")
HIDDEN_BROWSE_KINDS = ("plugin", "rule", "command", "hook")
KIND_META: dict[str, dict[str, str]] = {
    "skill": {
        "shelf": "brick",
        "what": "SOP 说明书（SKILL.md）；自己不执行",
        "install": "config/.../skills/（cap install --type skill）",
        "runs_in": "Agent 的 skill 元工具读入上下文",
        "local_install": "yes",
    },
    "mcp": {
        "shelf": "brick",
        "what": "连接器；真正可调的是发现出的 tools（≠ 市场 tool kind）",
        "install": "mcp_servers.json（cap install --type mcp）；桌面走 /api/mcp-gateway/relay/{name}/sse",
        "runs_in": "MCPManager / 能力级 HTTP 网关 / IDE",
        "local_install": "yes",
    },
    "tool": {
        "shelf": "brick",
        "what": "云端沙箱函数；≠ MCP tools，≠ 宿主 src/tools",
        "install": "无本地安装；仅 POST /api/runtime/tools/{name}/invoke",
        "runs_in": "runtime 沙箱 / workflow 节点",
        "local_install": "no",
    },
    "rule": {
        "shelf": "brick",
        "what": "持久指导（RULE.mdc）；alwaysApply / globs 控制何时生效",
        "install": "config/rules/<name>/（cap install --type rule）",
        "runs_in": "IDE / Agent 读入上下文",
        "local_install": "yes",
    },
    "command": {
        "shelf": "brick",
        "what": "可复用提示（COMMAND.md）；对话里用 / 唤起",
        "install": "config/commands/<name>/（cap install --type command）",
        "runs_in": "IDE / Agent 斜杠命令",
        "local_install": "yes",
    },
    "hook": {
        "shelf": "brick",
        "what": "生命周期脚本（hooks.json）；观察、拦截或跟进 Agent 事件",
        "install": "config/hooks/<name>/（cap install --type hook）",
        "runs_in": "IDE / 宿主 hook 运行时",
        "local_install": "yes",
    },
    "agent": {
        "shelf": "recipe",
        "what": "人设+依赖容器；日常靠 skill 说明书 + MCP tools",
        "install": "config/agents/<name>/（cap install）",
        "runs_in": "零号员工 / A2A；试用 runtime",
        "local_install": "yes",
    },
    "workflow": {
        "shelf": "recipe",
        "what": "已上架能力的静态 DAG（与 TEAM.md 平行）",
        "install": "不进 Agent 目录；无 cap install",
        "runs_in": "市场云端 workflows",
        "local_install": "no",
    },
    "plugin": {
        "shelf": "install",
        "what": "分发袋：拆 skill/mcp/rule/command/hook/可选 agent/tool",
        "install": "config/plugins/<name>/（cap install --type plugin）",
        "runs_in": "IDE / 本地引擎执行子组件",
        "local_install": "yes",
    },
}

# Agent 引用 skill/mcp 的三种来源（效果不同）
REF_WAYS = [
    {
        "id": "ref",
        "label": "dependencies.json 引用",
        "note": "独立上架商品；cap install agent 时按类型下载装入该 Agent 目录",
    },
    {
        "id": "embed",
        "label": "包内嵌 skills/ mcp/",
        "note": "只服务本 Agent；不拆行逛店；同名上架时详情 used_by",
    },
    {
        "id": "plugin_component",
        "label": "plugin 拆包",
        "note": "子项打 plugin-component；默认隐藏；装 plugin 时再装已发布子组件",
    },
]

ORCHESTRATION = {
    "team_pipeline": {
        "name": "团队流水线",
        "carrier": "TEAM.md",
        "nodes": "角色 assignee",
        "engine": "TeamOrchestrator",
        "lands_in_agent_config": True,
    },
    "capability_dag": {
        "name": "能力编排",
        "carrier": "workflow.json",
        "nodes": "tool|agent|skill|mcp",
        "engine": "market workflows",
        "lands_in_agent_config": False,
        "note": "禁止与 TEAM.md 节点互转；唯一组合点是 workflow 的 agent 节点调用 Agent",
    },
}

CONSUME_WAYS = [
    {"id": "sync", "label": "目录同步", "api": "GET /api/capabilities/sync"},
    {"id": "host_sync", "label": "宿主同步（已启用）", "api": "GET /api/my/host-sync"},
    {"id": "download", "label": "下载制品", "api": "GET /api/capabilities/{name}/download"},
    {"id": "install", "label": "本地组装（员工装机）", "api": "cap install"},
    {"id": "local", "label": "本地运行", "api": "cap run --mode local"},
    {
        "id": "online_gateway",
        "label": "模型线上网关（推荐，不装包）",
        "api": "marketplace_mcp → POST /api/runtime/*",
    },
    {"id": "trial", "label": "云端 runtime", "api": "POST /api/runtime/*"},
    {"id": "a2a", "label": "A2A 互调", "api": "Agent Card + tasks/send"},
    {"id": "mcp_bridge", "label": "市场 MCP 桥", "api": "marketplace_*"},
    {"id": "gateway", "label": "MCP HTTP 网关（管理员登记）", "api": "/api/mcp-gateway/{name}"},
    {
        "id": "cap_gateway",
        "label": "能力 MCP 网关（商品代理）",
        "api": "/api/mcp-gateway/relay/{name}/sse",
    },
    {"id": "join", "label": "加入我的能力", "api": "POST /api/my/capabilities"},
]

REVIEW_CHECKLIST = [
    "包结构与 schema 合法（按 kind 必需文件）",
    "无硬编码密钥（使用 ${VAR} / ${VAR:default} 占位）",
    "MCP command/url 可接受或已由管理员确认",
    "Tool 包通过 AST 安全审计",
    "依赖的积木存在且版本可解析",
    "Plugin 子组件命名不冲突；component 默认不单独上架浏览",
    "可见性符合组织策略（private/team/internal/public）",
]

INSTALL_POLICIES = ("optional", "default_on", "required")


def kinds_for_shelf(shelf: str) -> list[str] | None:
    """返回货架对应 kinds；未知货架返回 None。"""
    if not shelf:
        return None
    info = SHELVES.get(shelf)
    if not info:
        return None
    return list(info["kinds"])


def taxonomy_payload() -> dict[str, Any]:
    return {
        "shelves": SHELVES,
        "kinds": KIND_META,
        "default_browse_kinds": list(DEFAULT_BROWSE_KINDS),
        "more_browse_kinds": list(MORE_BROWSE_KINDS),
        "hidden_browse_kinds": list(HIDDEN_BROWSE_KINDS),
        "orchestration": ORCHESTRATION,
        "consume_ways": CONSUME_WAYS,
        "review_checklist": REVIEW_CHECKLIST,
        "install_policies": list(INSTALL_POLICIES),
        "ref_ways": REF_WAYS,
        "local_install_kinds": [
            k for k, meta in KIND_META.items() if meta.get("local_install") == "yes"
        ],
        "domain": {
            "asset": "逻辑能力（唯一 name）",
            "version": "Capability 行（name+version）；status 生命周期",
            "artifact": "能力包 zip + checksum",
            "relation": "depends_on / embeds / component_of / used_by（存于 input_schema 与 tags）",
            "owner": "author_id + organization",
            "note": (
                "type 字段即 kind；不必先拆表。"
                "主叙事：助手 + 依赖；逛店默认 agent+skill+mcp；"
                "plugin/rule/command/hook 保留能力、默认不逛店展示；"
                "更多仅 workflow/tool；"
                "市场 tool ≠ MCP tools ≠ 宿主 src/tools；"
                "cap install：agent/skill/mcp/plugin/rule/command/hook。"
            ),
        },
    }
