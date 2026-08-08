"""执行引擎：Agent 实例化 / 工具调用 / 技能激活 / MCP 安装与发现。"""

from typing import Any

from fastapi import APIRouter

from app.auth import CurrentUser, DbSession
from app.schemas import (
    CapabilityOut,
    RuntimeActivateRequest,
    RuntimeInstallRequest,
    RuntimeInvokeRequest,
    RuntimeResult,
)
from app.services.marketplace import (
    activate_skill,
    discover_mcp,
    install_mcp,
    instantiate_agent,
    invoke_tool,
    resolve_capability,
)
from app.services.capabilities import to_capability_out

router = APIRouter(prefix="/api/runtime", tags=["runtime"])


def _result(cap, action: str, message: str, result: dict[str, Any]) -> RuntimeResult:
    return RuntimeResult(
        capability=to_capability_out(cap),
        action=action,
        message=message,
        result=result,
    )


@router.post("/agents/{name}/instances", response_model=RuntimeResult)
async def instantiate(name: str, db: DbSession, user: CurrentUser, task: str = "执行任务"):
    cap = await resolve_capability(db, user, name)
    result = await instantiate_agent(db, user, cap, task)
    await db.commit()
    await db.refresh(cap)
    return _result(cap, "instantiate", f"Agent「{cap.name}」实例化成功", result)


@router.post("/tools/{name}/invoke", response_model=RuntimeResult)
async def invoke(name: str, data: RuntimeInvokeRequest, db: DbSession, user: CurrentUser):
    cap = await resolve_capability(db, user, name)
    result = await invoke_tool(db, user, cap, data.params)
    await db.commit()
    await db.refresh(cap)
    message = (
        f"工具「{cap.name}」调用成功（{result.get('execution')} 执行）"
        if result.get("status") == "ok"
        else f"工具「{cap.name}」执行失败：{result.get('error')}"
    )
    return _result(cap, "invoke", message, result)


@router.post("/skills/{name}/activate", response_model=RuntimeResult)
async def activate(name: str, data: RuntimeActivateRequest, db: DbSession, user: CurrentUser):
    cap = await resolve_capability(db, user, name)
    result = await activate_skill(db, user, cap, data.context)
    await db.commit()
    await db.refresh(cap)
    return _result(cap, "activate", f"技能「{cap.name}」激活成功", result)


@router.post("/mcp/{name}/install", response_model=RuntimeResult)
async def install(name: str, data: RuntimeInstallRequest, db: DbSession, user: CurrentUser):
    cap = await resolve_capability(db, user, name)
    result = await install_mcp(db, user, cap, data.config)
    await db.commit()
    await db.refresh(cap)
    return _result(cap, "install", f"MCP「{cap.name}」安装成功", result)


@router.get("/mcp/discover")
async def discover(db: DbSession, user: CurrentUser):
    tools = await discover_mcp(db, user)
    return {"discovered": tools}


@router.get("/health")
async def health():
    return {"status": "ok", "service": "marketplace-core"}
