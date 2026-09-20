"""工具在线编辑 API：读取 / 保存 schema.json（保存即新版本草稿）。"""

from fastapi import APIRouter

from app.auth import CurrentUser, DbSession, OptionalUser
from app.schemas import ToolEditOut, ToolEditSave
from app.services.capabilities import to_capability_out
from app.services.tool_editor import get_editable, save_version

router = APIRouter(prefix="/api/tools", tags=["tool-edit"])


def _to_out(cap, tool_schema: dict, implementation: str, files: list[dict], base_version: str) -> ToolEditOut:
    return ToolEditOut(
        capability=to_capability_out(cap),
        tool_schema=tool_schema,
        implementation=implementation,
        files=files,
        base_version=base_version,
    )


@router.get("/{name}/edit", response_model=ToolEditOut)
async def edit_tool(name: str, db: DbSession, user: OptionalUser):
    cap, schema, impl, files, base_version = await get_editable(db, user, name)
    return _to_out(cap, schema, impl, files, base_version)


@router.put("/{name}/edit", response_model=ToolEditOut)
async def save_tool_edit(name: str, data: ToolEditSave, db: DbSession, user: CurrentUser):
    cap, schema, impl, files = await save_version(db, user, name, data)
    _, _, _, _, base_version = await get_editable(db, user, name)
    return _to_out(cap, schema, impl, files, base_version)
