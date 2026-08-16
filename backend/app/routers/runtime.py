"""执行引擎：Agent 实例化 / 任务执行 / 工具调用 / 技能激活 / MCP 安装与发现。"""

import uuid
from typing import Any

from fastapi import APIRouter, HTTPException, status

from app.auth import CurrentUser, DbSession
from app.permissions import require_runtime_access
from app.schemas import (
    CapabilityOut,
    McpCallRequest,
    RuntimeInstantiateRequest,
    RuntimeTaskOut,
    RuntimeTaskRequest,
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
    record_usage,
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
async def instantiate(
    name: str,
    db: DbSession,
    user: CurrentUser,
    task: str = "执行任务",
    data: RuntimeInstantiateRequest | None = None,
):
    cap = await resolve_capability(db, user, name)
    await require_runtime_access(user, cap, db)
    if data is None:
        result = await instantiate_agent(db, user, cap, task)
    else:
        from app.services.bindings import resolve_binding

        binding = await resolve_binding(db, cap, data.binding) if data.binding else None
        result = await instantiate_agent(
            db,
            user,
            cap,
            data.task or task,
            binding=binding,
            adhoc=[d.model_dump() for d in data.bindings] or None,
        )
    await db.commit()
    await db.refresh(cap)
    return _result(cap, "instantiate", f"Agent「{cap.name}」实例化成功", result)


@router.post("/tools/{name}/invoke", response_model=RuntimeResult)
async def invoke(name: str, data: RuntimeInvokeRequest, db: DbSession, user: CurrentUser):
    cap = await resolve_capability(db, user, name)
    await require_runtime_access(user, cap, db)
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
    await require_runtime_access(user, cap, db)
    result = await activate_skill(db, user, cap, data.context)
    await db.commit()
    await db.refresh(cap)
    return _result(cap, "activate", f"技能「{cap.name}」激活成功", result)


@router.post("/mcp/{name}/install", response_model=RuntimeResult)
async def install(name: str, data: RuntimeInstallRequest, db: DbSession, user: CurrentUser):
    cap = await resolve_capability(db, user, name)
    await require_runtime_access(user, cap, db)
    result = await install_mcp(db, user, cap, data.config)
    await db.commit()
    await db.refresh(cap)
    return _result(cap, "install", f"MCP「{cap.name}」安装成功", result)


@router.post("/mcp/{name}/connect")
async def mcp_connect(name: str, db: DbSession, user: CurrentUser):
    """真实连接 MCP 能力包并发现其工具（调试/试用用）。"""
    from app.services.mcp_bridge import MCPBridge
    from app.services.mcp_gateway import load_gateway_config_by_name

    async def _gateway_loader(gw_name: str):
        try:
            return await load_gateway_config_by_name(db, gw_name)
        except Exception:  # noqa: BLE001
            return None

    cap = await resolve_capability(db, user, name)
    await require_runtime_access(user, cap, db)
    if cap.type != "mcp":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{name} 不是 MCP 能力")
    bridge = MCPBridge(gateway_loader=_gateway_loader)
    try:
        info = await bridge.connect_capability(name, cap)
        await record_usage(db, user, cap, "mcp_connect", {"tools": len(bridge.tool_defs)})
        await db.commit()
        return {
            "mcp": cap.name,
            "version": cap.version,
            "connected": info.get("connected"),
            "error": info.get("error", ""),
            "tools": [
                {
                    "name": t["function"]["name"],
                    "description": t["function"]["description"],
                    "inputSchema": t["function"]["parameters"],
                }
                  for t in bridge.tool_defs
              ],
        }
    finally:
        await bridge.close()


@router.post("/mcp/{name}/call")
async def mcp_call(name: str, data: McpCallRequest, db: DbSession, user: CurrentUser):
    """调用 MCP 能力包暴露的某个工具（调试/试用用，每次调用独立连接）。"""
    from app.services.mcp_bridge import MCPBridge
    from app.services.mcp_gateway import load_gateway_config_by_name

    async def _gateway_loader(gw_name: str):
        try:
            return await load_gateway_config_by_name(db, gw_name)
        except Exception:  # noqa: BLE001
            return None

    cap = await resolve_capability(db, user, name)
    await require_runtime_access(user, cap, db)
    if cap.type != "mcp":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{name} 不是 MCP 能力")
    bridge = MCPBridge(gateway_loader=_gateway_loader)
    try:
        await bridge.connect_capability(name, cap)
        if not bridge.has_tool(data.tool):
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"MCP「{name}」不存在工具 {data.tool}；可用工具：{', '.join(bridge.tool_names)}",
            )
        result = await bridge.call(data.tool, data.params)
        await record_usage(db, user, cap, "mcp_call", {"tool": data.tool, "params": data.params})
        await db.commit()
        return {"mcp": cap.name, "tool": data.tool, "result": result}
    finally:
        await bridge.close()


@router.get("/mcp/discover")
async def discover(db: DbSession, user: CurrentUser):
    tools = await discover_mcp(db, user)
    return {"discovered": tools}


@router.get("/health")
async def health():
    return {"status": "ok", "service": "marketplace-core"}


@router.post("/agents/{name}/tasks", response_model=RuntimeTaskOut)
async def run_agent_task(name: str, data: RuntimeTaskRequest, db: DbSession, user: CurrentUser):
    """直接向 Agent 发送任务：LLM 已配置时真实执行（工具沙箱 + 工具调用循环），否则模拟。"""
    from app.services.agent_runner import run_agent

    cap = await resolve_capability(db, user, name)
    await require_runtime_access(user, cap, db)
    if cap.type != "agent":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{name} 不是 Agent 能力")
    result = await run_agent(db, user, cap, data.task)
    return RuntimeTaskOut(
        task_id=str(uuid.uuid4()),
        agent=cap.name,
        version=cap.version,
        mode=result.get("mode", "simulated"),
        output=result.get("output", ""),
        tool_calls=int(result.get("tool_calls", 0)),
        runtime=result.get("runtime", {}),
        steps=result.get("steps", []),
    )
