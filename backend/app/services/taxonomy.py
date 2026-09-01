"""能力市场货架与 kind 语义（控制面目录，不是六个并列商店）。

type 字段 = kind；门户按 shelf（积木 / 配方 / 安装包）组织。
"""

from __future__ import annotations

from typing import Any

SHELVES: dict[str, dict[str, Any]] = {
    "brick": {
        "key": "brick",
        "label": "积木",
        "description": "Skill / MCP（及供编排节点用的 Tool）。给配方与安装包引用。",
        "kinds": ["skill", "mcp", "tool"],
    },
    "recipe": {
        "key": "recipe",
        "label": "配方",
        "description": "Agent（可含 TEAM.md 团队流水线）与能力编排 Workflow 平行。",
        "kinds": ["agent", "workflow"],
    },
    "install": {
        "key": "install",
        "label": "安装包",
        "description": "Plugin：Agent Plugins 一键分发。不是通道类 src/plugins。",
        "kinds": ["plugin"],
    },
}

KIND_SHELF: dict[str, str] = {
    kind: shelf_key
    for shelf_key, shelf in SHELVES.items()
    for kind in shelf["kinds"]
}

# 浏览默认：安装包 + 配方（不含积木；积木需显式筛选）
DEFAULT_BROWSE_KINDS = ("plugin", "agent", "workflow")

KIND_META: dict[str, dict[str, str]] = {
    "skill": {
        "shelf": "brick",
        "what": "可复用 SOP（SKILL.md）",
        "install": "config/.../skills/",
        "runs_in": "Agent skill 工具激活",
    },
    "mcp": {
        "shelf": "brick",
        "what": "连接器；发现出的 tools 可调",
        "install": "mcp_servers.json",
        "runs_in": "MCPManager / 网关 / IDE",
    },
    "tool": {
        "shelf": "brick",
        "what": "沙箱函数，主要给能力编排节点",
        "install": "仅云端 invoke，不写 src/tools",
        "runs_in": "runtime 沙箱 / workflow 节点",
    },
    "agent": {
        "shelf": "recipe",
        "what": "人设+依赖；可选 TEAM.md 团队流水线",
        "install": "config/agents/<name>/",
        "runs_in": "零号员工 / A2A；试用 runtime",
    },
    "workflow": {
        "shelf": "recipe",
        "what": "已上架能力的静态 DAG（与 TEAM.md 平行）",
        "install": "不进 Agent 目录",
        "runs_in": "市场云端 workflows",
    },
    "plugin": {
        "shelf": "install",
        "what": "Agent Plugins 分发包",
        "install": "拆 component；Cursor 装 skills+mcp",
        "runs_in": "IDE / 本地引擎执行子组件",
    },
}

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
    {"id": "download", "label": "下载制品", "api": "GET /api/capabilities/{name}/download"},
    {"id": "install", "label": "本地组装", "api": "cap install"},
    {"id": "local", "label": "本地运行", "api": "cap run --mode local"},
    {"id": "trial", "label": "云端试用", "api": "POST /api/runtime/*"},
    {"id": "a2a", "label": "A2A 互调", "api": "Agent Card + tasks/send"},
    {"id": "mcp_bridge", "label": "市场 MCP 桥", "api": "marketplace_*"},
    {"id": "gateway", "label": "MCP HTTP 网关", "api": "/api/mcp-gateway/{name}"},
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
        "orchestration": ORCHESTRATION,
        "consume_ways": CONSUME_WAYS,
        "review_checklist": REVIEW_CHECKLIST,
        "install_policies": list(INSTALL_POLICIES),
        "domain": {
            "asset": "逻辑能力（唯一 name）",
            "version": "Capability 行（name+version）；status 生命周期",
            "artifact": "能力包 zip + checksum",
            "relation": "depends_on / embeds / component_of / used_by（存于 input_schema 与 tags）",
            "owner": "author_id + organization",
            "note": "type 字段即 kind；不必先拆表",
        },
    }
