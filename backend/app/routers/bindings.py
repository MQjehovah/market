"""Agent 动态绑定 API：给 agent 人设绑定工具/技能/MCP，运行时实时组装。"""

from fastapi import APIRouter, HTTPException, status

from app.auth import CurrentUser, DbSession
from app.models import AgentBinding, Capability
from app.schemas import AgentBindingCreate, AgentBindingOut, AgentBindingUpdate
from app.services.bindings import (
    create_binding,
    delete_binding,
    get_binding,
    list_bindings,
    update_binding,
)
from app.services.marketplace import resolve_capability

router = APIRouter(prefix="/api/agents", tags=["bindings"])


def _to_out(binding: AgentBinding, agent: Capability) -> AgentBindingOut:
    return AgentBindingOut(
        id=binding.id,
        agent_id=binding.agent_id,
        agent_name=agent.name,
        name=binding.name,
        description=binding.description or "",
        version=binding.version,
        dependencies=list(binding.dependencies or []),
        enabled=binding.enabled,
        created_by=binding.created_by,
        created_at=binding.created_at,
        updated_at=binding.updated_at,
    )


async def _resolve_agent(db: DbSession, user, name: str) -> Capability:
    cap = await resolve_capability(db, user, name)
    if cap.type != "agent":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{name} 不是 Agent 能力")
    return cap


@router.get("/{name}/bindings", response_model=list[AgentBindingOut])
async def agent_bindings(name: str, db: DbSession, user: CurrentUser):
    agent = await _resolve_agent(db, user, name)
    return [_to_out(b, agent) for b in await list_bindings(db, agent)]


@router.post("/{name}/bindings", response_model=AgentBindingOut, status_code=status.HTTP_201_CREATED)
async def create_agent_binding(
    name: str, data: AgentBindingCreate, db: DbSession, user: CurrentUser
):
    agent = await _resolve_agent(db, user, name)
    binding = await create_binding(db, user, agent, data)
    return _to_out(binding, agent)


@router.get("/{name}/bindings/{binding_id}", response_model=AgentBindingOut)
async def agent_binding_detail(
    name: str, binding_id: str, db: DbSession, user: CurrentUser
):
    agent = await _resolve_agent(db, user, name)
    binding = await get_binding(db, user, binding_id, agent)
    return _to_out(binding, agent)


@router.put("/{name}/bindings/{binding_id}", response_model=AgentBindingOut)
async def update_agent_binding(
    name: str,
    binding_id: str,
    data: AgentBindingUpdate,
    db: DbSession,
    user: CurrentUser,
):
    agent = await _resolve_agent(db, user, name)
    binding = await get_binding(db, user, binding_id, agent)
    binding = await update_binding(db, binding, data)
    return _to_out(binding, agent)


@router.delete("/{name}/bindings/{binding_id}", status_code=status.HTTP_204_NO_CONTENT)
async def remove_agent_binding(name: str, binding_id: str, db: DbSession, user: CurrentUser):
    agent = await _resolve_agent(db, user, name)
    binding = await get_binding(db, user, binding_id, agent)
    await delete_binding(db, binding)
    return None
