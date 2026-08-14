"""Agent 编辑 API：读取/保存提示词与绑定能力（保存即新版本草稿）。"""

from fastapi import APIRouter, HTTPException, status

from app.auth import CurrentUser, DbSession, OptionalUser
from app.schemas import AgentEditOut, AgentEditSave
from app.services.agent_editor import get_editable, save_version
from app.services.capabilities import to_capability_out

router = APIRouter(prefix="/api/agents", tags=["agent-edit"])


def _require_publisher(user) -> None:
    if user.role not in ("admin", "publisher"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "普通用户无权限编辑 Agent，请联系管理员开通发布权限",
        )


def _to_out(cap, prompt: str, deps: list[dict], base_version: str) -> AgentEditOut:
    return AgentEditOut(
        capability=to_capability_out(cap),
        prompt=prompt,
        dependencies=deps,
        base_version=base_version,
    )


@router.get("/{name}/edit", response_model=AgentEditOut)
async def edit_agent(name: str, db: DbSession, user: OptionalUser):
    cap, prompt, deps, base_version = await get_editable(db, user, name)
    return _to_out(cap, prompt, deps, base_version)


@router.put("/{name}/edit", response_model=AgentEditOut)
async def save_agent_edit(name: str, data: AgentEditSave, db: DbSession, user: CurrentUser):
    _require_publisher(user)
    cap, prompt, deps = await save_version(db, user, name, data)
    _, _, _, base_version = await get_editable(db, user, name)
    return _to_out(cap, prompt, deps, base_version)
