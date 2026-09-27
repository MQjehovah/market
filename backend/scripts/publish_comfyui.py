"""发布 ComfyUI 连接器到 market（type=mcp, v1.0.0, published）。

在容器内运行： ``python scripts/publish_comfyui.py``
实现读取 /app/backend/scripts/comfyui_remote.py。
"""

import asyncio
import io
import json
import zipfile

from sqlalchemy import select

from app.database import SessionLocal
from app.models import Capability, User
from app.services.capability_package import apply_package

NAME = "comfyui"
VERSION = "1.0.0"
DISPLAY = "ComfyUI 图像"
DESC = (
    "远程 ComfyUI 调用：服务器状态/硬件显存、模型与节点查询、上传输入图、提交工作流并等待、"
    "取回输出与图片、中断/清空队列/释放显存、SD 文生图。"
)

TOOLS = [
    ("get_comfyui_server", "获取当前 ComfyUI 服务器地址"),
    ("set_comfyui_server", "切换目标 ComfyUI 服务器地址"),
    ("get_server_status", "查询 ComfyUI 服务器状态（版本/系统内存/显存/队列）"),
    ("list_models", "列出远程 ComfyUI 上的模型文件"),
    ("search_nodes", "搜索远程 ComfyUI 节点（含自定义节点）"),
    ("get_node_info", "获取指定节点的输入/输出定义"),
    ("validate_workflow", "提交前校验工作流（节点/参数/连接有效性）"),
    ("upload_image", "上传输入图片到远程 ComfyUI input 目录"),
    ("run_workflow", "提交工作流到远程 ComfyUI 执行（可选等待并取回结果）"),
    ("get_job_status", "查询某个 prompt_id 的执行状态与进度"),
    ("wait_for_job", "阻塞等待某个 prompt_id 执行结束"),
    ("get_outputs", "获取输出文件清单（可选下载/内联图片）"),
    ("get_queue_status", "查看当前执行与排队任务"),
    ("interrupt", "中断当前正在执行的任务"),
    ("clear_queue", "清空等待队列"),
    ("free_memory", "释放显存"),
    ("generate_image", "SD 文生图（CheckpointLoader+KSampler+SaveImage 快捷出图）"),
]

README = """# comfyui（ComfyUI 图像）

通过 ComfyUI 原生 HTTP API 远程驱动目标机器上的 ComfyUI。

## 工具
- 状态/环境：`get_server_status`、`set_comfyui_server`、`get_comfyui_server`
- 模型/节点：`list_models`、`search_nodes`、`get_node_info`
- 工作流：`validate_workflow`、`run_workflow`、`get_job_status`、`wait_for_job`、`get_outputs`
- 队列/显存：`get_queue_status`、`interrupt`、`clear_queue`、`free_memory`
- 出图：`upload_image`、`generate_image`

## 环境变量
- `COMFYUI_URL`：目标 ComfyUI 地址，默认 http://192.168.31.34:8188
- `COMFYUI_TIMEOUT`：单次 HTTP 超时（秒），默认 30
- `COMFYUI_INLINE_BYTES`：单张图片内联返回上限（字节），默认 5MB
"""


def build_zip(impl: str) -> bytes:
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
    mcp_meta = {"name": NAME, "description": "图像生成", "version": VERSION}
    tools = {"tools": [{"name": n, "description": d} for n, d in TOOLS]}
    security = {"notes": "通过 COMFYUI_URL 指向目标 ComfyUI；无密钥。内网地址按需配置。"}
    files = {
        "connection.json": json.dumps(connection, ensure_ascii=False, indent=2),
        "mcp.json": json.dumps(mcp_meta, ensure_ascii=False, indent=2),
        "tools.json": json.dumps(tools, ensure_ascii=False, indent=2),
        "security.json": json.dumps(security, ensure_ascii=False, indent=2),
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
    content = build_zip(impl)

    async with SessionLocal() as db:
        exists = await db.scalar(
            select(Capability.id).where(Capability.name == NAME, Capability.type == "mcp")
        )
        if exists:
            print("已存在同名能力", NAME)
            return
        # 以现有 mcp 能力(mail)作者作为发布者
        ref = await db.scalar(
            select(Capability).where(Capability.name == "mail", Capability.type == "mcp")
        )
        author = await db.get(User, ref.author_id) if ref else None
        if author is None:
            print("找不到发布者账号")
            return

        cap = Capability(
            name=NAME,
            type="mcp",
            version=VERSION,
            status="published",
            description=DESC,
            display_name=DISPLAY,
            category="图像生成",
            tags=["图像", "AIGC", "ComfyUI"],
            visibility="internal",
            author_id=author.id,
            organization=author.department,
            access_policy="open",
            install_policy="optional",
            distribution="both",
            risk_default="write",
            binding="service",
        )
        db.add(cap)
        await db.flush()
        await apply_package(db, author, cap, content, filename=f"{NAME}-{VERSION}.zip")
        await db.commit()
        print(f"published {NAME} v{VERSION}")
        print("tools:", len((cap.input_schema or {}).get("tools") or []))
        print("required_env:", (cap.input_schema or {}).get("required_env"))


if __name__ == "__main__":
    asyncio.run(main())
