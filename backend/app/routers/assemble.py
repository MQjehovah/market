"""Agent 组装 API：人设 + 工具/技能/MCP 依赖 → 生成 agent 能力。"""

from fastapi import APIRouter

from app.auth import CurrentUser, DbSession
from app.schemas import AssembleRequest, CapabilityOut
from app.services.assemble import assemble_agent
from app.services.capabilities import to_capability_out

router = APIRouter(prefix="/api/assemble", tags=["assemble"])


@router.post("/agents", response_model=CapabilityOut, status_code=201)
async def create_assembled_agent(data: AssembleRequest, db: DbSession, user: CurrentUser):
    cap = await assemble_agent(db, user, data)
    return to_capability_out(cap, author_name=user.username)
