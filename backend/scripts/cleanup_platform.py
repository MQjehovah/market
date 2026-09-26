"""平台清洗（一次性）：修复插件组件断链 + 回填显示名/描述。

在容器内运行： ``python scripts/cleanup_platform.py``
"""

import asyncio
import collections
import copy

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Capability
from app.services.capabilities import parse_semver

# 插件组件按名称重定向（旧名 -> 现名）
NAME_ALIAS = {
    ("mcp", "erp_api"): ("mcp", "erp"),
    ("agent", "device-ops"): ("agent", "设备运维"),
}

# 已发布能力补显示名
RELABEL = {
    ("mcp", "dingtalk"): "钉钉",
    ("mcp", "erp"): "ERP",
    ("mcp", "jira"): "Jira",
    ("mcp", "gitlab"): "GitLab",
    ("mcp", "gerrit"): "Gerrit",
    ("mcp", "mysql_query"): "MySQL 查询",
    ("mcp", "remote_terminal"): "远程终端",
    ("mcp", "remote_operation"): "远程操作",
    ("mcp", "default"): "默认服务",
    ("mcp", "ticket_ops"): "工单运维",
    ("mcp", "time"): "时间",
    ("mcp", "marketplace"): "能力市场",
    ("plugin", "digital-hub"): "数字中台能力包",
    ("plugin", "工单处理"): "工单处理",
    ("agent", "数字中台"): "数字中台",
    ("agent", "设备运维"): "设备运维",
    ("agent", "代码审查"): "代码审查",
    ("agent", "售后客服"): "售后客服",
    ("agent", "测试工程师"): "测试工程师",
    ("agent", "IT运维"): "IT运维",
    ("agent", "AI开发团队"): "AI开发团队",
    ("skill", "ticket-handling"): "工单处理",
    ("skill", "product-business-analysis"): "产品经营分析",
    ("skill", "monthly-business-review"): "月度经营分析",
    ("skill", "report-writer"): "报告撰写",
    ("tool", "web_fetch"): "网页抓取",
    ("tool", "web_search"): "网页搜索",
    ("tool", "file"): "文件操作",
    ("tool", "edit"): "文件编辑",
    ("tool", "glob"): "文件匹配",
    ("tool", "grep"): "内容搜索",
    ("tool", "shell"): "终端命令",
    ("tool", "memory"): "记忆",
    ("tool", "subagent"): "子代理",
    ("tool", "todowrite"): "任务清单",
    ("tool", "code_search"): "代码搜索",
    ("tool", "batch_edit"): "批量编辑",
    ("tool", "ask_user"): "询问用户",
}

# 已发布能力补描述
DESC = {
    ("agent", "设备运维"): (
        "面向设备远程运维的专家：覆盖设备上下线、碰撞/离线/退货等异常处置、现场取证与"
        "远程操作，协同远程终端与工单工具完成问题闭环。"
    ),
}


async def main() -> None:
    async with SessionLocal() as db:
        caps = (await db.scalars(select(Capability))).all()
        by: dict[tuple[str, str], list[Capability]] = collections.defaultdict(list)
        for c in caps:
            by[(c.type, c.name)].append(c)

        def current(t: str, n: str) -> Capability | None:
            rows = [c for c in by.get((t, n), []) if c.status in ("published", "deprecated")]
            return max(rows, key=lambda c: parse_semver(c.version)) if rows else None

        changed = 0
        # 1) 插件组件断链重连（deepcopy 后再赋值，确保 JSON 变更被 SQLAlchemy 检测）
        for p in [c for c in caps if c.type == "plugin"]:
            schema = copy.deepcopy(p.input_schema or {})
            comps = schema.get("components") or []
            dirty = False
            for comp in comps:
                if not isinstance(comp, dict):
                    continue
                t, n = comp.get("type"), comp.get("name")
                tgt = current(t, n)
                if tgt is None and (t, n) in NAME_ALIAS:
                    tgt = current(*NAME_ALIAS[(t, n)])
                if tgt is not None and comp.get("capability_id") != tgt.id:
                    comp["capability_id"] = tgt.id
                    comp["version"] = tgt.version
                    comp["name"] = tgt.name
                    dirty = True
            if dirty:
                schema["components"] = comps
                p.input_schema = schema
                changed += 1
                print("relinked plugin:", p.name)

        # 2) 显示名回填
        for (t, n), label in RELABEL.items():
            for c in by.get((t, n), []):
                if c.status == "published" and not (c.display_name or "").strip():
                    c.display_name = label

        # 3) 描述回填
        for (t, n), text in DESC.items():
            for c in by.get((t, n), []):
                if c.status == "published" and not (c.description or "").strip():
                    c.description = text

        await db.commit()
        print("done. relinked_plugins=%d" % changed)


if __name__ == "__main__":
    asyncio.run(main())
