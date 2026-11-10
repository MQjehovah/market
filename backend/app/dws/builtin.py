"""内置 dingtalk-dws 工具能力（云端执行入口，不是可下载包）。"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Capability, User

DWS_CAPABILITY_NAME = "dingtalk-dws"

CONFIG_UI = {
    "auth": "device_code",
    "provider": "dws",
    "auto_prompt_on_join": True,
    "title": "钉钉 DWS 登录",
    "subtitle": "每个人使用自己的钉钉身份；凭据只保存在云端工作区，不进入数据库。",
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


async def ensure_dws_capability(db: AsyncSession) -> Capability:
    """幂等创建内置 DWS 工具能力；已存在则原样返回。"""
    existing = await db.scalar(
        select(Capability)
        .where(Capability.name == DWS_CAPABILITY_NAME, Capability.type == "tool")
        .order_by(Capability.created_at.desc())
    )
    if existing is not None:
        # 老版本内置能力补齐声明的 UI/执行器（不改用户自定义值）
        if not existing.executor:
            existing.executor = "dws_cli"
        if not existing.config_ui:
            existing.config_ui = CONFIG_UI
        return existing
    admin = await db.scalar(
        select(User).where(User.role == "admin").order_by(User.created_at.asc())
    )
    author = admin or await db.scalar(select(User).order_by(User.created_at.asc()))
    if author is None:
        raise RuntimeError("无法创建 dingtalk-dws：没有可用作者用户")
    cap = Capability(
        name=DWS_CAPABILITY_NAME,
        display_name="钉钉工作台 DWS",
        slug=DWS_CAPABILITY_NAME,
        type="tool",
        version="1.0.0",
        description=(
            "按当前用户的钉钉身份执行 DWS 工作台命令：消息、日程、文档、待办、通讯录、"
            "邮件、审批、AI 表格、知识库、白板等。凭据由用户扫码授权并按人隔离。"
        ),
        status="published",
        category="办公协同",
        tags=["钉钉", "DWS", "云端"],
        visibility="public",
        access_policy="open",
        install_policy="optional",
        distribution="remote",
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
    return cap
