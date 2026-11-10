"""钉钉 DWS MCP 能力：一个市场能力，内部工具由 MCP server 提供。"""

from __future__ import annotations

import io
import json
import logging
import zipfile

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.dws.workspace import DwsWorkspace
from app.models import Capability, CapabilityArtifact, User
from app.services.secret_vault import upsert_secrets_bulk
from app.storage import get_storage

logger = logging.getLogger("market.dws.builtin")

DWS_CAPABILITY_NAME = "dingtalk-dws"
DWS_LEGACY_SKILL_NAME = "dingtalk-dws-skill-legacy"
DWS_LEGACY_TOOL_NAME = "dingtalk-dws-legacy"
OPENAPI_NAME = "dingtalk-openapi"
OPENAPI_LEGACY_NAME = "dingtalk"
DWS_DISPLAY_NAME = "钉钉 DWS"
DWS_DESCRIPTION = (
    "钉钉 DWS（工作台 CLI）：使用员工自己的钉钉个人授权，按人隔离凭据；"
    "桌面端本机登录，网页端在能力配置页扫码授权。通过 MCP 暴露 dws_schema / "
    "dws_read / dws_write 工具，覆盖消息、日程、文档、待办、通讯录、邮件、审批、"
    "AI 表格、知识库、白板等业务。"
)
OPENAPI_DISPLAY_NAME = "钉钉开放接口"
OPENAPI_DESCRIPTION = (
    "钉钉开放接口（MCP）：使用零号员工机器人/企业应用身份调用钉钉开放平台接口，"
    "全用户共用服务身份，不使用员工个人授权。"
)
CONFIG_UI = {
    "auth": "device_code",
    "provider": "dws",
    "auto_prompt_on_join": True,
    # DWS 的 DWS_CONFIG_DIR 等是平台托管的 workspace 路径，不是用户要填的凭据
    "hide_env_fields": True,
    "title": "钉钉 DWS 登录",
    "subtitle": "使用你自己的钉钉身份；凭据只保存在云端工作区，不进入数据库。",
    "note": "电脑端和网页端同时使用同一个钉钉账号时，refresh token 可能互相踢下线；被踢后重新扫码即可。",
}
ENV_KEYS = ("DWS_CONFIG_DIR", "DWS_KEYCHAIN_DIR", "DWS_WORKDIR")
INPUT_SCHEMA = {
    "kind": "mcp",
    "name": DWS_CAPABILITY_NAME,
    "version": "1.0.3",
    "description": DWS_DESCRIPTION,
    "transport": "stdio",
    "command": "python",
    "args": ["-m", "app.dws.mcp_server"],
    "required_env": list(ENV_KEYS),
    "env": {
        "DWS_CONFIG_DIR": "/app/backend/data/workspaces/{user_id}/dws/config",
        "DWS_KEYCHAIN_DIR": "/app/backend/data/workspaces/{user_id}/dws/keychain",
        "DWS_WORKDIR": "/app/backend/data/workspaces/{user_id}/dws/files",
    },
}
README = """# 钉钉 DWS

使用每个员工自己的钉钉 DWS 个人授权。通过 MCP 工具调用：

- `dws_schema`：查询命令和参数；
- `dws_read`：只读命令；
- `dws_write`：写命令，必须先获得用户确认并传 `confirm=true`。

网页端首次使用前，在能力详情页「配置」Tab 扫码授权；桌面端继续使用本机 `dws auth login`。
"""


def _skill_zip() -> bytes:
    connection = {
        "transport": "stdio",
        "command": "python",
        "args": ["-m", "app.dws.mcp_server"],
        "cwd": "/app/backend",
        "env": {
            "DWS_CONFIG_DIR": "${DWS_CONFIG_DIR}",
            "DWS_KEYCHAIN_DIR": "${DWS_KEYCHAIN_DIR}",
            "DWS_WORKDIR": "${DWS_WORKDIR}",
        },
    }
    meta = {
        "name": DWS_CAPABILITY_NAME,
        "display_name": DWS_DISPLAY_NAME,
        "description": DWS_DESCRIPTION,
        "version": "1.0.3",
        "transport": "stdio",
    }
    files = {
        "mcp.json": json.dumps(meta, ensure_ascii=False, indent=2).encode(),
        "connection.json": json.dumps(connection, ensure_ascii=False, indent=2).encode(),
        "tools.json": b"[]",
        "security.json": b"{}",
        "README.md": README.encode("utf-8"),
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, content in files.items():
            zf.writestr(name, content)
    return buf.getvalue()


async def _author(db: AsyncSession) -> User | None:
    admin = await db.scalar(
        select(User).where(User.role == "admin").order_by(User.created_at.asc())
    )
    return admin or await db.scalar(select(User).order_by(User.created_at.asc()))


async def _archive_skill_rows(db: AsyncSession, name: str) -> int:
    rows = (
        await db.scalars(
            select(Capability).where(Capability.name == name, Capability.type == "skill")
        )
    ).all()
    for row in rows:
        # 用 id 后缀避免历史遗留版本同名同版本冲突
        row.name = f"{DWS_LEGACY_SKILL_NAME}-{row.id[:8]}"
        row.status = "archived"
    if rows:
        logger.info("钉钉 DWS：归档旧 skill %s 共 %d 个版本", name, len(rows))
    return len(rows)


async def _rename_legacy_names(db: AsyncSession) -> None:
    await _archive_skill_rows(db, DWS_CAPABILITY_NAME)
    await _archive_skill_rows(db, "dingtalk-cli")
    # 更早的内部执行工具
    await db.execute(
        text(
            "UPDATE capabilities SET name=:new, status='archived' "
            "WHERE name='dingtalk-dws' AND type='tool'"
        ),
        {"new": DWS_LEGACY_TOOL_NAME},
    )
    # 开放接口旧名
    res = await db.execute(
        text("UPDATE capabilities SET name=:new WHERE name=:old AND type='mcp'"),
        {"new": OPENAPI_NAME, "old": OPENAPI_LEGACY_NAME},
    )
    if res.rowcount:
        for table, column in (
            ("user_capabilities", "capability_name"),
            ("ratings", "capability_name"),
            ("subscriptions", "capability_name"),
            ("user_secrets", "scope"),
            ("capability_secrets", "capability_name"),
            ("capability_collaborators", "capability_name"),
        ):
            try:
                await db.execute(
                    text(f"UPDATE {table} SET {column}=:new WHERE {column}=:old"),
                    {"new": OPENAPI_NAME, "old": OPENAPI_LEGACY_NAME},
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("开放接口引用迁移失败 %s.%s: %s", table, column, exc)


async def _ensure_dws_mcp(db: AsyncSession) -> Capability:
    rows = (
        await db.scalars(
            select(Capability)
            .where(Capability.name == DWS_CAPABILITY_NAME, Capability.type == "mcp")
            .order_by(Capability.created_at.desc())
        )
    ).all()
    cap = next((r for r in rows if r.version == "1.0.3"), None)
    if cap is None:
        for old in rows:
            if old.status == "published":
                old.status = "deprecated"
        author = await _author(db)
        if author is None:
            raise RuntimeError("无法创建钉钉 DWS：没有可用作者用户")
        cap = Capability(
            name=DWS_CAPABILITY_NAME,
            display_name=DWS_DISPLAY_NAME,
            slug=DWS_CAPABILITY_NAME,
            type="mcp",
            version="1.0.3",
            description=DWS_DESCRIPTION,
            status="published",
            category="办公协同",
            tags=["钉钉", "DWS", "个人授权", "MCP"],
            visibility="public",
            access_policy="open",
            install_policy="optional",
            distribution="remote",
            binding="user",
            risk_default="destructive",
            data_domain="钉钉",
            input_schema=INPUT_SCHEMA,
            config_ui=CONFIG_UI,
            verified=True,
            author_id=author.id,
            organization=author.department or "",
            requires={"binary": "dws", "min_version": "1.0.62"},
        )
        db.add(cap)
        await db.flush()
        content = _skill_zip()
        info = get_storage().save(cap.id, f"{DWS_CAPABILITY_NAME}-1.0.3.zip", io.BytesIO(content))
        db.add(CapabilityArtifact(capability_id=cap.id, filename=f"{DWS_CAPABILITY_NAME}-1.0.3.zip", **info))
    else:
        cap.display_name = DWS_DISPLAY_NAME
        cap.description = DWS_DESCRIPTION
        cap.input_schema = INPUT_SCHEMA
        cap.config_ui = CONFIG_UI
        cap.binding = "user"
        cap.distribution = "remote"
        cap.risk_default = "destructive"
        cap.data_domain = "钉钉"
    return cap


async def _provision_paths(db: AsyncSession) -> None:
    """把每用户的 DWS 目录作为运行环境变量托管，供 MCP server 注入。

    值只是目录路径，不是 token；使用 user_secrets 是为了复用现有的用户级 env 注入链路。
    """
    ws = DwsWorkspace()
    users = (await db.scalars(select(User).where(User.is_active.is_(True)))).all()
    for user in users:
        ws.ensure(user.id)
        await upsert_secrets_bulk(
            db,
            user.id,
            secrets={
                "DWS_CONFIG_DIR": str(ws.config_dir(user.id)),
                "DWS_KEYCHAIN_DIR": str(ws.keychain_dir(user.id)),
                "DWS_WORKDIR": str(ws.files_dir(user.id)),
            },
            scope=DWS_CAPABILITY_NAME,
        )
    logger.info("钉钉 DWS：已为 %d 个用户准备 workspace 路径", len(users))


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
    """幂等：归档旧 skill/工具，创建唯一 dingtalk-dws MCP，并准备用户路径。"""
    await _rename_legacy_names(db)
    cap = await _ensure_dws_mcp(db)
    await _label_openapi(db)
    await _provision_paths(db)
    return cap
