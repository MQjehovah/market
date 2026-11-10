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
    "在能力配置页扫码授权后，网页端和桌面端都按个人身份执行。通过 MCP 暴露 "
    "dws_schema / dws_read / dws_write 工具，覆盖消息、日程、文档、待办、"
    "通讯录、邮件、审批、AI 表格、知识库、白板等业务。"
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

使用**每个员工自己的钉钉个人授权**执行钉钉工作台操作。

## 能做什么

- 消息、群聊、日程、文档、待办、通讯录
- 邮件、审批、AI 表格、知识库、白板
- 支持查询、发送、创建、修改；写操作会要求确认

## 使用前：扫码授权

1. 加入「钉钉 DWS」；
2. 在能力详情页打开「配置」；
3. 点「扫码授权」，用钉钉扫码并确认；
4. 状态变为「已授权」后，回到 Agent 对话即可使用。

凭据保存在云端个人工作区，不写入数据库，也不会被其他员工使用。管理员不能代替员工扫码，也不能读取别人的个人消息。

## 怎么用

直接在对话里描述任务，例如：

- 「查一下我今天的日程」
- 「把这条消息发到项目群」
- 「从知识库找报销制度」
- 「创建一个明天下午三点的待办」

网页端和桌面端都按当前登录员工的个人授权执行；换人使用时会使用各自已授权的钉钉身份。

## 常见问题

- **一直显示未授权**：重新打开「配置」扫码；二维码过期后刷新即可。
- **昨天还能用，今天被踢下线**：电脑端和网页端同时登录同一钉钉账号时，refresh token 可能互相失效；重新扫码一次即可。
- **找不到某个群或文档**：先确认你的钉钉账号本身有该群/文档权限。
"""

OPENAPI_README = """# 钉钉开放接口

使用**零号员工机器人/企业应用身份**调用钉钉开放平台接口。

## 能做什么

- 以机器人身份发送群消息、单聊消息
- 查询群、通讯录、审批、日程等企业开放接口
- 由管理员维护平台凭据，普通用户无需配置个人钉钉授权

## 使用前：平台凭据

该能力由管理员在「配置」中维护平台凭据；配置完成后所有被授权用户共用同一服务身份。

普通员工不需要扫码，也不需要填写个人钉钉账号。若提示「云端共享凭据还没配齐」，请联系管理员配置。

## 怎么用

直接在对话里描述任务，例如：

- 「在项目群发一条通知」
- 「查一下这个审批的状态」
- 「把会议纪要发到群里」

## 和「钉钉 DWS」的区别

- **钉钉开放接口**：机器人/企业应用身份，适合发通知、查企业数据；
- **钉钉 DWS**：员工个人身份，适合查“我的”消息、日程、待办。

## 注意

下游看到的是机器人，不是员工本人；不会读取员工个人消息。需要个人身份时请使用「钉钉 DWS」。
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
            readme_md=README,
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
        cap.readme_md = README
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
        row.readme_md = OPENAPI_README


async def ensure_dingtalk_capabilities(db: AsyncSession) -> Capability:
    """幂等：归档旧 skill/工具，创建唯一 dingtalk-dws MCP，并准备用户路径。"""
    await _rename_legacy_names(db)
    cap = await _ensure_dws_mcp(db)
    await _label_openapi(db)
    await _provision_paths(db)
    return cap
