"""内置钉钉能力元数据：DWS 是 skill（个人授权），开放接口是 MCP（机器人身份）。"""

from __future__ import annotations

import io
import json
import logging
import zipfile

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Capability, CapabilityArtifact, User
from app.storage import get_storage

logger = logging.getLogger("market.dws.builtin")

DWS_CAPABILITY_NAME = "dingtalk-dws"
DWS_SKILL_LEGACY_NAME = "dingtalk-cli"
DWS_TOOL_LEGACY_NAME = "dingtalk-dws-legacy"
DWS_DISPLAY_NAME = "钉钉 DWS"
DWS_DESCRIPTION = (
    "钉钉 DWS（工作台 CLI）技能：使用员工自己的钉钉个人授权，按人隔离凭据；"
    "桌面端本机登录，网页端在能力配置页扫码授权。覆盖消息、日程、文档、待办、"
    "通讯录、邮件、审批、AI 表格、知识库、白板等业务。"
)
OPENAPI_NAME = "dingtalk-openapi"
OPENAPI_LEGACY_NAME = "dingtalk"
OPENAPI_DISPLAY_NAME = "钉钉开放接口"
OPENAPI_DESCRIPTION = (
    "钉钉开放接口（MCP）：使用零号员工机器人/企业应用身份调用钉钉开放平台接口，"
    "全用户共用服务身份，不使用员工个人授权。"
)

CONFIG_UI = {
    "auth": "device_code",
    "provider": "dws",
    "auto_prompt_on_join": True,
    "title": "钉钉 DWS 登录",
    "subtitle": "使用你自己的钉钉身份；凭据只保存在云端工作区，不进入数据库。",
    "note": "电脑端和网页端同时使用同一个钉钉账号时，refresh token 可能互相踢下线；被踢后重新扫码即可。",
}

INPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "DWS 子命令，如 chat message list、calendar event list",
        },
        "args": {"type": "object", "description": "具名参数（按 dws schema）"},
        "positional": {
            "type": "array",
            "items": {"type": "string"},
            "description": "位置参数",
        },
        "mode": {
            "type": "string",
            "enum": ["read", "write", "destructive"],
            "description": "调用方声明风险，必须不低于命令实际 effect",
        },
        "confirm": {"type": "boolean", "description": "写操作必须为 true"},
        "profile": {"type": "string", "description": "多 profile 时指定 corpId:userId"},
        "inputs": {
            "type": "array",
            "items": {"type": "object"},
            "description": "[{arg, file_id}] 上传文件并替换参数",
        },
        "outputs": {
            "type": "array",
            "items": {"type": "object"},
            "description": "[{arg, name}] 命令输出文件落点",
        },
    },
    "required": ["command", "mode"],
}

SKILL_MD = """# 钉钉 DWS

使用当前员工的钉钉 DWS（工作台 CLI）个人授权执行钉钉操作。

## 网页端

1. 未授权时，提示用户打开本能力详情页「配置」Tab 扫码授权。
2. 用 `dws_schema` 查询命令和参数。
3. 读操作用 `dws` 工具，`mode=read`。
4. 写操作先向用户说明影响并征得同意，再用 `mode=write` / `mode=destructive` + `confirm=true`。
5. 不要用 shell 执行 `dws`；网页端由 market 按用户 workspace 执行。

## 桌面端

继续使用本机 `dws` CLI 和 `dws auth login`；凭据保存在本机钥匙串。
"""


async def _author(db: AsyncSession) -> User | None:
    admin = await db.scalar(
        select(User).where(User.role == "admin").order_by(User.created_at.asc())
    )
    return admin or await db.scalar(select(User).order_by(User.created_at.asc()))


def _skill_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("SKILL.md", SKILL_MD.encode("utf-8"))
        zf.writestr(
            "skill.json",
            json.dumps(
                {
                    "name": DWS_CAPABILITY_NAME,
                    "display_name": DWS_DISPLAY_NAME,
                    "description": DWS_DESCRIPTION,
                    "version": "1.0.0",
                    "requires": {"binary": "dws", "min_version": "1.0.62"},
                },
                ensure_ascii=False,
                indent=2,
            ).encode("utf-8"),
        )
    return buf.getvalue()


async def _rename_refs(db: AsyncSession, old: str, new: str) -> None:
    refs = (
        ("user_capabilities", "capability_name"),
        ("ratings", "capability_name"),
        ("subscriptions", "capability_name"),
        ("user_secrets", "scope"),
        ("capability_secrets", "capability_name"),
        ("capability_collaborators", "capability_name"),
    )
    for table, column in refs:
        try:
            await db.execute(
                text(f"UPDATE {table} SET {column}=:new WHERE {column}=:old"),
                {"old": old, "new": new},
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("DWS 能力名迁移失败 %s.%s %s→%s: %s", table, column, old, new, exc)


async def _migrate_names(db: AsyncSession) -> None:
    # 旧的内部执行工具先让出 dingtalk-dws 名字
    res = await db.execute(
        text(
            "UPDATE capabilities SET name=:new, status='archived' "
            "WHERE name='dingtalk-dws' AND type='tool'"
        ),
        {"new": DWS_TOOL_LEGACY_NAME},
    )
    if res.rowcount:
        await _rename_refs(db, "dingtalk-dws", DWS_TOOL_LEGACY_NAME)
    # 现有 skill 改为正式名 dingtalk-dws
    res = await db.execute(
        text("UPDATE capabilities SET name=:new WHERE name=:old AND type='skill'"),
        {"new": DWS_CAPABILITY_NAME, "old": DWS_SKILL_LEGACY_NAME},
    )
    if res.rowcount:
        await _rename_refs(db, DWS_SKILL_LEGACY_NAME, DWS_CAPABILITY_NAME)
    # 开放接口 MCP 改为 dingtalk-openapi
    res = await db.execute(
        text("UPDATE capabilities SET name=:new WHERE name=:old AND type='mcp'"),
        {"new": OPENAPI_NAME, "old": OPENAPI_LEGACY_NAME},
    )
    if res.rowcount:
        await _rename_refs(db, OPENAPI_LEGACY_NAME, OPENAPI_NAME)


async def _ensure_dws_skill(db: AsyncSession) -> Capability:
    rows = (
        await db.scalars(
            select(Capability)
            .where(Capability.name == DWS_CAPABILITY_NAME, Capability.type == "skill")
            .order_by(Capability.created_at.desc())
        )
    ).all()
    cap = rows[0] if rows else None
    if cap is None:
        author = await _author(db)
        if author is None:
            raise RuntimeError("无法创建钉钉 DWS：没有可用作者用户")
        cap = Capability(
            name=DWS_CAPABILITY_NAME,
            display_name=DWS_DISPLAY_NAME,
            slug=DWS_CAPABILITY_NAME,
            type="skill",
            version="1.0.0",
            description=DWS_DESCRIPTION,
            status="published",
            category="办公协同",
            tags=["钉钉", "DWS", "个人授权"],
            visibility="public",
            access_policy="open",
            install_policy="optional",
            distribution="both",
            binding="user_only",
            risk_default="destructive",
            data_domain="钉钉",
            input_schema=INPUT_SCHEMA,
            config_ui=CONFIG_UI,
            executor="dws_cli",
            verified=True,
            author_id=author.id,
            organization=author.department or "",
            requires={"binary": "dws", "min_version": "1.0.62"},
        )
        db.add(cap)
        await db.flush()
        content = _skill_zip()
        info = get_storage().save(cap.id, f"{DWS_CAPABILITY_NAME}-1.0.0.zip", io.BytesIO(content))
        db.add(CapabilityArtifact(capability_id=cap.id, filename=f"{DWS_CAPABILITY_NAME}-1.0.0.zip", **info))
        return cap

    # 已有 skill：只补能力元数据，不覆盖用户已发布的 SKILL.md
    for row in rows:
        row.display_name = DWS_DISPLAY_NAME
        row.description = DWS_DESCRIPTION
        row.executor = "dws_cli"
        row.config_ui = CONFIG_UI
        row.binding = "user_only"
        row.input_schema = INPUT_SCHEMA
        row.risk_default = "destructive"
        row.data_domain = "钉钉"
    return cap


async def _label_openapi(db: AsyncSession) -> None:
    rows = (
        await db.scalars(
            select(Capability).where(Capability.name == OPENAPI_NAME, Capability.type == "mcp")
        )
    ).all()
    for row in rows:
        row.display_name = OPENAPI_DISPLAY_NAME
        row.description = OPENAPI_DESCRIPTION


async def ensure_dingtalk_capabilities(db: AsyncSession) -> Capability:
    """幂等：名称迁移 + DWS skill 元数据 + 开放接口 MCP 展示名。"""
    await _migrate_names(db)
    cap = await _ensure_dws_skill(db)
    await _label_openapi(db)
    return cap
