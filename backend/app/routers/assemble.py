"""Agent 组装 API：人设 + 工具/技能/MCP 依赖 → 生成 agent 能力。"""

from fastapi import APIRouter, HTTPException, status

from app.auth import CurrentUser, DbSession
from app.schemas import AssembleRequest, CapabilityOut
from app.services.assemble import assemble_agent
from app.services.capabilities import to_capability_out

router = APIRouter(prefix="/api/assemble", tags=["assemble"])


def _require_publisher(user) -> None:
    if user.role not in ("admin", "publisher"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "普通用户无权限组装发布 Agent，请联系管理员开通发布权限",
        )


@router.post("/agents", response_model=CapabilityOut, status_code=status.HTTP_201_CREATED)
async def create_assembled_agent(data: AssembleRequest, db: DbSession, user: CurrentUser):
    _require_publisher(user)
    cap = await assemble_agent(db, user, data)
    return to_capability_out(cap, author_name=user.username)
