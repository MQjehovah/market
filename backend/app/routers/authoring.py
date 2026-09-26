"""AI 创作助手 API：按类型用 LLM 生成/润色能力内容草稿（写权限=capability.publish）。

不落库；前端拿到生成内容后填进编辑器，仍走各编辑器的保存 → 提交审核流程。
"""

from fastapi import APIRouter, HTTPException, status

from app.auth import CurrentUser
from app.permissions import role_has_permission
from app.schemas import AuthoringOut, AuthoringRequest
from app.services import authoring

router = APIRouter(prefix="/api/authoring", tags=["authoring"])


@router.post("/generate", response_model=AuthoringOut)
async def generate(data: AuthoringRequest, user: CurrentUser) -> AuthoringOut:
    if not role_has_permission(user.role, "capability.publish"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要发布权限（Publisher/Admin）")
    body = await authoring.generate(
        kind=data.kind,
        name=data.name,
        description=data.description,
        instruction=data.instruction,
        current=data.current,
    )
    return AuthoringOut(**body)
