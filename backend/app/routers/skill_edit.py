"""技能在线编辑 API：读取 / 保存 SKILL.md（保存即新版本草稿）。"""

from fastapi import APIRouter, HTTPException, status

from app.auth import CurrentUser, DbSession, OptionalUser
from app.schemas import SkillEditOut, SkillEditSave
from app.services.capabilities import to_capability_out
from app.services.skill_editor import get_editable, save_version

router = APIRouter(prefix="/api/skills", tags=["skill-edit"])


def _to_out(cap, skill_md: str, files: list[dict], base_version: str) -> SkillEditOut:
    return SkillEditOut(
        capability=to_capability_out(cap),
        skill_md=skill_md,
        files=files,
        base_version=base_version,
    )


@router.get("/{name}/edit", response_model=SkillEditOut)
async def edit_skill(name: str, db: DbSession, user: OptionalUser):
    cap, skill_md, files, base_version = await get_editable(db, user, name)
    return _to_out(cap, skill_md, files, base_version)


@router.put("/{name}/edit", response_model=SkillEditOut)
async def save_skill_edit(name: str, data: SkillEditSave, db: DbSession, user: CurrentUser):
    cap, skill_md, files = await save_version(db, user, name, data)
    _, _, _, base_version = await get_editable(db, user, name)
    return _to_out(cap, skill_md, files, base_version)
