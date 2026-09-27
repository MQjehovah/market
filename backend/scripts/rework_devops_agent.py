"""修复 market「设备运维」专家(v1.1.2)：
- dependencies.json / agent.json.dependencies 改用当前能力名：
  remote_operation→rosiwit-cloud-remote-api、ticket_ops→rosiwit-cloud-ticket、
  remote_terminal→rosiwit-cloud-remote-terminal、default→mail；
- agent.json / mcp_servers.json 本地 server 名同步：remote_operation→rosiwit_cloud_remote、
  ticket_ops→rosiwit_cloud_ticket、default→mail(args→mcp_server/src/mail.py)；
- PROMPT.md 文本同步旧名→新名。

在容器内运行： ``python scripts/rework_devops_agent.py``
"""

import asyncio
import io
import json
import zipfile

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.database import SessionLocal
from app.models import Capability, User
from app.services.capabilities import (
    draft_policy_kwargs,
    next_version,
    parse_semver,
    read_capability_files,
)
from app.services.capability_package import apply_package

NEW_DEPS = [
    {"name": "rosiwit-cloud-remote-api", "type": "mcp", "version": "1.0.0"},
    {"name": "rosiwit-cloud-ticket", "type": "mcp", "version": "1.0.0"},
    {"name": "rosiwit-cloud-remote-terminal", "type": "mcp", "version": "1.0.3"},
    {"name": "dingtalk", "type": "mcp", "version": "1.0.6"},
    {"name": "mail", "type": "mcp", "version": "1.2.0"},
]

# 本地 server 名 + 参数改写
SERVER_RENAME = {
    "default": ("mail", "mcp_server/src/default.py", "mcp_server/src/mail.py"),
    "remote_operation": ("rosiwit_cloud_remote", "mcp_server/src/remote_operation.py", "mcp_server/src/rosiwit_cloud_remote.py"),
    "ticket_ops": ("rosiwit_cloud_ticket", "mcp_server/src/ticket_ops.py", "mcp_server/src/rosiwit_cloud_ticket.py"),
}
TEXT_REPLACE = [
    ("remote_operation", "rosiwit_cloud_remote"),
    ("ticket_ops", "rosiwit_cloud_ticket"),
]


def _fix_servers(entries) -> None:
    if not isinstance(entries, list):
        return
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name")
        if name in SERVER_RENAME:
            new_name, old_arg, new_arg = SERVER_RENAME[name]
            entry["name"] = new_name
            args = entry.get("args")
            if isinstance(args, list):
                entry["args"] = [new_arg if a == old_arg else a for a in args]
        desc = entry.get("description")
        if isinstance(desc, str):
            for a, b in TEXT_REPLACE:
                desc = desc.replace(a, b)
            entry["description"] = desc


def _rework_files(files: dict[str, bytes]) -> dict[str, bytes]:
    out = dict(files)

    # dependencies.json
    out["dependencies.json"] = json.dumps(NEW_DEPS, ensure_ascii=False, indent=2).encode("utf-8")

    # agent.json
    raw = files.get("agent.json")
    if raw:
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            data = {}
        if isinstance(data, dict):
            data["dependencies"] = NEW_DEPS
            _fix_servers(data.get("mcp_servers"))
            desc = data.get("description")
            if isinstance(desc, str):
                for a, b in TEXT_REPLACE:
                    desc = desc.replace(a, b)
                data["description"] = desc
            out["agent.json"] = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    # mcp_servers.json (顶层数组)
    raw = files.get("mcp_servers.json")
    if raw:
        try:
            data = json.loads(raw.decode("utf-8"))
        except ValueError:
            data = None
        if isinstance(data, list):
            _fix_servers(data)
            out["mcp_servers.json"] = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")

    # PROMPT.md
    raw = files.get("PROMPT.md")
    if raw:
        text = raw.decode("utf-8", "replace")
        for a, b in TEXT_REPLACE:
            text = text.replace(a, b)
        out["PROMPT.md"] = text.encode("utf-8")

    return out


def _zip(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in files.items():
            z.writestr(name, data)
    return buf.getvalue()


async def main() -> None:
    async with SessionLocal() as db:
        rows = list(
            (
                await db.scalars(
                    select(Capability)
                    .options(selectinload(Capability.artifacts))
                    .where(Capability.name == "设备运维", Capability.type == "agent")
                )
            ).all()
        )
        if not rows:
            print("未找到 设备运维 agent")
            return
        published = [r for r in rows if r.status == "published"]
        base = max(published or rows, key=lambda c: parse_semver(c.version))
        new_version = next_version(base.version, "patch")
        if any(r.version == new_version for r in rows):
            print("已存在版本", new_version)
            return
        author = await db.get(User, base.author_id)

        files = _rework_files(read_capability_files(base))
        content = _zip(files)

        new = Capability(
            name="设备运维",
            type="agent",
            version=new_version,
            status="published",
            description=base.description,
            display_name=base.display_name,
            category=base.category,
            tags=list(base.tags or []),
            visibility=base.visibility,
            author_id=base.author_id,
            organization=base.organization,
            binding=getattr(base, "binding", None) or "service",
            **draft_policy_kwargs(base),
        )
        db.add(new)
        await db.flush()
        await apply_package(db, author, new, content, filename=f"设备运维-{new_version}.zip")

        for r in rows:
            if r.id != new.id and r.status == "published":
                r.status = "deprecated"
        await db.commit()
        print(f"published 设备运维 v{new_version}; files={len(files)}")
        print("deps:", [d["name"] for d in NEW_DEPS])


if __name__ == "__main__":
    asyncio.run(main())
