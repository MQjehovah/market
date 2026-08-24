"""MCP 在线编辑 API：读取 / 保存 connection.json（保存即新版本草稿）。"""

from fastapi import APIRouter

from app.auth import CurrentUser, DbSession, OptionalUser
from app.schemas import McpEditOut, McpEditSave
from app.services.capabilities import to_capability_out
from app.services.mcp_editor import get_editable, save_version

router = APIRouter(prefix="/api/mcp", tags=["mcp-edit"])


def _to_out(
    cap, connection: dict, tools_json, files: list[dict], base_version: str
) -> McpEditOut:
    return McpEditOut(
        capability=to_capability_out(cap),
        connection=connection,
        tools_json=tools_json,
        files=files,
        base_version=base_version,
    )


@router.get("/{name}/edit", response_model=McpEditOut)
async def edit_mcp(name: str, db: DbSession, user: OptionalUser):
    cap, connection, tools, files, base_version = await get_editable(db, user, name)
    return _to_out(cap, connection, tools, files, base_version)


@router.put("/{name}/edit", response_model=McpEditOut)
async def save_mcp_edit(name: str, data: McpEditSave, db: DbSession, user: CurrentUser):
    cap, connection, tools, files = await save_version(db, user, name, data)
    _, _, _, _, base_version = await get_editable(db, user, name)
    return _to_out(cap, connection, tools, files, base_version)
