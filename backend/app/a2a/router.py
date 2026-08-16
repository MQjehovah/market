"""A2A 路由：Agent Card 发现 + JSON-RPC 任务接口。"""

from fastapi import APIRouter, HTTPException, Request, status

from app.a2a.protocol import (
    A2ATask,
    A2ATaskCancelParams,
    A2ATaskGetParams,
    A2ATaskSendParams,
    AgentCard,
    AgentSkill,
    JsonRpcError,
    JsonRpcRequest,
    JsonRpcResponse,
    extract_text,
)
from app.a2a.service import (
    build_agent_card,
    cancel_task,
    get_a2a_agents,
    get_task,
    resolve_a2a_agent,
    send_task,
    to_protocol_task,
)
from app.auth import CurrentUser, DbSession, OptionalUser
from app.models import Capability
from app.permissions import require_runtime_access

router = APIRouter(prefix="/api/a2a", tags=["a2a"])
well_known_router = APIRouter(tags=["a2a"])


def _err(id, code: int, message: str, data=None) -> JsonRpcResponse:
    return JsonRpcResponse(id=id, error=JsonRpcError(code=code, message=message, data=data))


@router.get("/agents", response_model=list[AgentCard])
async def discover_agents(request: Request, db: DbSession, user: OptionalUser):
    """发现平台内所有可互调的 Agent（返回各自 Agent Card）。"""
    agents = await get_a2a_agents(db, user)
    base_url = str(request.base_url).rstrip("/")
    cards = []
    for agent in agents:
        cards.append(build_agent_card(agent, base_url=base_url))
    return cards


@router.get("/agents/{cap_id}/card", response_model=AgentCard)
async def agent_card(request: Request, cap_id: str, db: DbSession, user: OptionalUser):
    agent = await resolve_a2a_agent(db, user, cap_id)
    return build_agent_card(agent, base_url=str(request.base_url).rstrip("/"))


@router.get("/agents/{cap_id}", response_model=AgentCard)
async def agent_card_short(request: Request, cap_id: str, db: DbSession, user: OptionalUser):
    """兼容标准发现路径：GET /agents/{id} 返回 Agent Card。"""
    return await agent_card(request, cap_id, db, user)


@router.get("/agents/{cap_id}/a2a", response_model=AgentCard)
async def agent_card_at_endpoint(request: Request, cap_id: str, db: DbSession, user: OptionalUser):
    """GET card.url 路径也返回 Agent Card，便于发现（A2A 规范）。"""
    return await agent_card(request, cap_id, db, user)


@router.post("/agents/{cap_id}/a2a", response_model=JsonRpcResponse)
async def jsonrpc_endpoint(cap_id: str, request: JsonRpcRequest, db: DbSession, user: CurrentUser):
    """A2A JSON-RPC 2.0 接口：tasks/send | tasks/get | tasks/cancel。"""
    try:
        agent = await resolve_a2a_agent(db, user, cap_id)
    except HTTPException:
        return _err(request.id, -32003, "Agent 不存在或未启用 A2A", {"agent_id": cap_id})
    method = request.method

    if method == "tasks/send":
        await require_runtime_access(user, agent, db)
        try:
            params = A2ATaskSendParams.model_validate(request.params)
        except Exception as exc:
            return _err(request.id, -32602, "参数不合法", str(exc))
        task = await send_task(
            db,
            user,
            agent,
            client_task_id=params.id,
            message=params.message.model_dump(by_alias=True),
            metadata=params.metadata,
        )
        return JsonRpcResponse(id=request.id, result=to_protocol_task(task).model_dump(by_alias=True))

    if method == "tasks/get":
        try:
            params = A2ATaskGetParams.model_validate(request.params)
        except Exception as exc:
            return _err(request.id, -32602, "参数不合法", str(exc))
        try:
            task = await get_task(db, user, params.id)
        except HTTPException as exc:
            return _err(request.id, -32001, "任务不存在" if exc.status_code == 404 else str(exc.detail), {"task_id": params.id})
        return JsonRpcResponse(id=request.id, result=to_protocol_task(task).model_dump(by_alias=True))

    if method == "tasks/cancel":
        try:
            params = A2ATaskCancelParams.model_validate(request.params)
        except Exception as exc:
            return _err(request.id, -32602, "参数不合法", str(exc))
        task = await cancel_task(db, user, params.id, params.reason)
        return JsonRpcResponse(id=request.id, result=to_protocol_task(task).model_dump(by_alias=True))

    return _err(request.id, -32601, f"不支持的方法：{method}")


@router.get("/tasks/{task_id}", response_model=A2ATask)
async def get_task_rest(task_id: str, db: DbSession, user: CurrentUser):
    task = await get_task(db, user, task_id)
    return to_protocol_task(task)


async def build_meta_card(request: Request, db: DbSession, user: OptionalUser) -> AgentCard:
    """市场元卡片：把平台本身暴露为一个 A2A Agent，技能 = 平台内所有已发布 Agent。"""
    agents = await get_a2a_agents(db, user)
    skills = [
        AgentSkill(
            id=a.id,
            name=a.name,
            description=a.description or "",
            tags=a.tags or [],
            examples=[f"委派任务给 {a.name} v{a.version}"],
        )
        for a in agents
    ]
    return build_agent_card(
        Capability(organization="平台部", id="marketplace", name="AI 能力公共市场"),
        base_url=str(request.base_url).rstrip("/"),
        meta=True,
        skills=skills,
    )


@router.get("/meta-card", response_model=AgentCard)
async def meta_card(request: Request, db: DbSession, user: OptionalUser):
    return await build_meta_card(request, db, user)


@well_known_router.get("/.well-known/agent-card.json", response_model=AgentCard)
async def well_known_agent_card(request: Request, db: DbSession, user: OptionalUser):
    """A2A 标准发现路径：返回市场元卡片。"""
    return await build_meta_card(request, db, user)
