"""把 market 的 mail 能力重打包为 v1.2.0：implementation/mail.py（SMTP 发送 + IMAP 收发）。

在容器内运行： ``python scripts/rework_mail_full.py``
- 读取 /app/backend/scripts/mail.py 作为实现；
- 新建 published 版本 1.2.0（沿用授权/安装策略），旧 published 版本置 deprecated。
"""

import asyncio
import io
import json
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.models import Capability, User
from app.services.capabilities import draft_policy_kwargs, parse_semver
from app.services.capability_package import apply_package

NEW_VERSION = "1.2.0"
DESC = (
    "邮件服务（SMTP 发送 + IMAP 收发）：发送/抄送/密送/附件、列文件夹、列邮件、条件检索、"
    "读信（正文+附件清单）、下载附件、未读数、标记已读/未读、移动/复制/删除、新建/删除文件夹、回复。"
)

TOOLS = [
    ("send_email", "发送邮件（收件人/主题/正文/HTML/抄送/密送/附件/回复地址）"),
    ("list_folders", "列出邮箱所有文件夹"),
    ("list_messages", "列出文件夹最近邮件（可按未读/日期/发件人/主题过滤）"),
    ("search_messages", "IMAP 条件检索邮件（发件人/收件人/主题/正文/日期/未读/已读/旗标）"),
    ("read_message", "读取单封邮件（正文 text/html + 附件清单）"),
    ("download_attachments", "下载某封邮件的附件到本机目录"),
    ("get_unread_count", "获取文件夹未读邮件数"),
    ("set_message_flags", "设置邮件标记（已读/未读/旗标/已回复）"),
    ("move_message", "移动邮件到目标文件夹"),
    ("copy_message", "复制邮件到目标文件夹"),
    ("delete_message", "删除邮件（标记删除或彻底删除）"),
    ("create_folder", "新建文件夹"),
    ("delete_folder", "删除文件夹"),
    ("reply_email", "回复某封邮件（自动带 Re: 主题与收件人）"),
]

README = """# mail（邮件服务）

SMTP 发送 + IMAP 收发，常见邮件能力一站式。

## 工具
- `send_email`：发送邮件（to / subject / body / is_html / cc / bcc / attachments / reply_to）
- `list_folders`：列文件夹
- `list_messages`：列最近邮件（folder / limit / unread_only / since / before / sender / subject）
- `search_messages`：条件检索（from_addr / to_addr / subject / body / since / before / unseen / seen / flagged）
- `read_message`：读单封（返回 text/html 与附件清单；可 mark_seen）
- `download_attachments`：下载附件到本机目录（save_dir / attachment_index）
- `get_unread_count`：未读数
- `set_message_flags`：标记已读/未读/旗标（add / remove）
- `move_message` / `copy_message`：移动 / 复制
- `delete_message`：删除（permanently 控制是否彻底删除）
- `create_folder` / `delete_folder`：文件夹管理
- `reply_email`：回复（reply_all 可带原抄送）

## 环境变量
- 发送：`SMTP_HOST` / `SMTP_PORT` / `SMTP_USERNAME` / `SMTP_PASSWORD` / `SMTP_FROM_NAME`
- 收取：`IMAP_HOST`（默认 imap.qiye.aliyun.com）/ `IMAP_PORT`（默认 993）；
  `IMAP_USERNAME` / `IMAP_PASSWORD` 缺省复用 `SMTP_USERNAME` / `SMTP_PASSWORD`
- 下载默认目录：`MAIL_ATTACH_DIR`

阿里企业邮箱需在「设置-客户端设置」开启 IMAP/SMTP 并使用**客户端专用密码（授权码）**，非登录密码。
"""


def build_zip(mail_py: str) -> bytes:
    connection = {
        "name": "mail",
        "transport": "stdio",
        "command": "python",
        "args": ["implementation/mail.py"],
        "env": {
            "SMTP_HOST": "smtp.qiye.aliyun.com",
            "SMTP_PORT": "465",
            "SMTP_USERNAME": "",
            "SMTP_PASSWORD": "${SMTP_PASSWORD}",
            "SMTP_FROM_NAME": "",
            "IMAP_HOST": "imap.qiye.aliyun.com",
            "IMAP_PORT": "993",
        },
    }
    mcp_meta = {"name": "mail", "description": "邮件", "version": NEW_VERSION}
    tools = {"tools": [{"name": n, "description": d} for n, d in TOOLS]}
    security = {"notes": "凭据用 ${VAR} 占位，交由市场平台密钥或本机环境变量配置。"}
    files = {
        "connection.json": json.dumps(connection, ensure_ascii=False, indent=2),
        "mcp.json": json.dumps(mcp_meta, ensure_ascii=False, indent=2),
        "tools.json": json.dumps(tools, ensure_ascii=False, indent=2),
        "security.json": json.dumps(security, ensure_ascii=False, indent=2),
        "README.md": README,
        "implementation/mail.py": mail_py,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


async def main() -> None:
    with open("/app/backend/scripts/mail.py", encoding="utf-8") as fh:
        mail_py = fh.read()
    content = build_zip(mail_py)

    async with SessionLocal() as db:
        rows = list(
            (
                await db.scalars(
                    select(Capability)
                    .options(selectinload(Capability.artifacts))
                    .where(Capability.name == "mail", Capability.type == "mcp")
                )
            ).all()
        )
        if not rows:
            print("未找到 mail 能力")
            return
        if any(r.version == NEW_VERSION for r in rows):
            print("已存在版本", NEW_VERSION)
            return
        published = [r for r in rows if r.status == "published"]
        base = max(published or rows, key=lambda c: parse_semver(c.version))
        author = await db.get(User, base.author_id)

        new = Capability(
            name="mail",
            type="mcp",
            version=NEW_VERSION,
            status="published",
            description=DESC,
            display_name="邮件",
            category=base.category,
            tags=list(base.tags or []),
            visibility=base.visibility,
            author_id=base.author_id,
            organization=base.organization,
            binding=getattr(base, "binding", None) or "service",
            slug=getattr(base, "slug", "") or "",
            requires=getattr(base, "requires", None) or {},
            provenance=getattr(base, "provenance", None) or {},
            verified=bool(getattr(base, "verified", False)),
            **draft_policy_kwargs(base),
        )
        db.add(new)
        await db.flush()

        await apply_package(db, author, new, content, filename=f"mail-{NEW_VERSION}.zip")

        for r in rows:
            if r.id != new.id and r.status == "published":
                r.status = "deprecated"
        new.description = DESC
        new.display_name = "邮件"
        await db.commit()
        print(f"published mail v{NEW_VERSION}; deprecated {sum(1 for r in rows if r.status=='deprecated')}")
        print("input_schema.tools:", len((new.input_schema or {}).get("tools") or []))
        print("required_env:", (new.input_schema or {}).get("required_env"))


if __name__ == "__main__":
    asyncio.run(main())
