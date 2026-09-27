"""更新 market 的 comfyui 能力到 v1.1.0：支持列出/读取/直接运行服务器已保存工作流 + 修复 UI→API 控件映射与子图展开。

在容器内运行： ``python scripts/update_comfyui.py``
实现读取 /app/backend/scripts/comfyui_remote.py；工具清单读取 /app/backend/scripts/comfyui_tools.json。
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

NAME = "comfyui"
NEW_VERSION = "1.1.0"

README = """# comfyui（ComfyUI 图像）

通过 ComfyUI 原生 HTTP API 远程驱动目标机器上的 ComfyUI。

## 用法：优先复用服务器上已调好的工作流
1. `list_saved_workflows` 列出服务器已保存的工作流（前端「工作流」列表）。
2. `run_saved_workflow(name=..., prompt=..., seed=...)` 直接运行，并按需覆盖提示词/种子/步数等。
3. 需要精细改结构：`get_saved_workflow(name)` 看节点，`run_saved_workflow(node_overrides={"<id>": {"<输入>": 值}})` 精确覆盖。

## 其他工具
- 状态/环境：`get_server_status`、`set/get_comfyui_server`
- 模型/节点：`list_models`、`search_nodes`、`get_node_info`
- 工作流：`validate_workflow`、`run_workflow`、`get_job_status`、`wait_for_job`、`get_outputs`
- 队列/显存：`get_queue_status`、`interrupt`、`clear_queue`、`free_memory`
- 出图：`upload_image`、`generate_image`

## 环境变量
- `COMFYUI_URL`：目标 ComfyUI 地址，默认 http://192.168.31.34:8188
- `COMFYUI_TIMEOUT`：单次 HTTP 超时（秒），默认 30
- `COMFYUI_INLINE_BYTES`：单张图片内联返回上限（字节），默认 5MB
"""


def build_zip(impl: str, tools: list) -> bytes:
    connection = {
        "name": NAME,
        "transport": "stdio",
        "command": "python",
        "args": ["implementation/comfyui_remote.py"],
        "env": {
            "COMFYUI_URL": "http://192.168.31.34:8188",
            "COMFYUI_TIMEOUT": "30",
            "COMFYUI_INLINE_BYTES": "5242880",
        },
    }
    mcp_meta = {"name": NAME, "description": "图像生成", "version": NEW_VERSION}
    files = {
        "connection.json": json.dumps(connection, ensure_ascii=False, indent=2),
        "mcp.json": json.dumps(mcp_meta, ensure_ascii=False, indent=2),
        "tools.json": json.dumps({"tools": tools}, ensure_ascii=False, indent=2),
        "security.json": json.dumps(
            {"notes": "通过 COMFYUI_URL 指向目标 ComfyUI；无密钥。内网地址按需配置。"},
            ensure_ascii=False, indent=2,
        ),
        "README.md": README,
        "implementation/comfyui_remote.py": impl,
    }
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, content in files.items():
            z.writestr(name, content)
    return buf.getvalue()


async def main() -> None:
    with open("/app/backend/scripts/comfyui_remote.py", encoding="utf-8") as fh:
        impl = fh.read()
    with open("/app/backend/scripts/comfyui_tools.json", encoding="utf-8") as fh:
        tools = json.load(fh)
    content = build_zip(impl, tools)

    async with SessionLocal() as db:
        rows = list(
            (
                await db.scalars(
                    select(Capability)
                    .options(selectinload(Capability.artifacts))
                    .where(Capability.name == NAME, Capability.type == "mcp")
                )
            ).all()
        )
        if not rows:
            print("未找到 comfyui 能力")
            return
        if any(r.version == NEW_VERSION for r in rows):
            print("已存在版本", NEW_VERSION)
            return
        published = [r for r in rows if r.status == "published"]
        base = max(published or rows, key=lambda c: parse_semver(c.version))
        author = await db.get(User, base.author_id)

        new = Capability(
            name=NAME,
            type="mcp",
            version=NEW_VERSION,
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
        await apply_package(db, author, new, content, filename=f"{NAME}-{NEW_VERSION}.zip")

        for r in rows:
            if r.id != new.id and r.status == "published":
                r.status = "deprecated"
        await db.commit()
        print(f"published {NAME} v{NEW_VERSION}; tools={len(tools)}")


if __name__ == "__main__":
    asyncio.run(main())
