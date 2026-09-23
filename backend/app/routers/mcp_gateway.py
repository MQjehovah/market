"""MCP 中转网关管理：网关注册表 CRUD 与连接测试。"""

import logging

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select

from app.auth import CurrentUser, DbSession
from app.models import Capability, MCPGatewayServer
from app.schemas import (
    MCPGatewayServerIn,
    MCPGatewayServerOut,
    MCPGatewayTestOut,
    MessageOut,
)
from app.services.mcp_gateway import probe_tools, row_to_config

router = APIRouter(prefix="/api/admin/mcp-gateway", tags=["mcp-gateway"])
logger = logging.getLogger("market.mcp_gateway.admin")


def _require_admin(user) -> None:
    if user.role != "admin":
        raise HTTPException(status.HTTP_403_FORBIDDEN, "仅管理员可管理 MCP 网关")


async def _validate_capability_binding(
    db: DbSession, capability_id: str, server_id: str | None = None
) -> None:
    """校验能力绑定：必须是存在的 mcp 能力，且未被其他网关服务绑定。"""
    if not capability_id:
        return
    cap = await db.get(Capability, capability_id)
    if cap is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "绑定的能力不存在")
    if cap.type != "mcp":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "只能绑定 mcp 类型的能力")
    stmt = select(MCPGatewayServer.id).where(
        MCPGatewayServer.capability_id == capability_id
    )
    if server_id is not None:
        stmt = stmt.where(MCPGatewayServer.id != server_id)
    dup = await db.scalar(stmt)
    if dup:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"能力 {cap.name} 已绑定到其他网关服务"
        )


@router.get("/servers", response_model=list[MCPGatewayServerOut])
async def list_servers(db: DbSession, user: CurrentUser):
    _require_admin(user)
    rows = (await db.scalars(select(MCPGatewayServer).order_by(MCPGatewayServer.created_at.desc()))).all()
    return list(rows)


@router.post("/servers", response_model=MCPGatewayServerOut, status_code=status.HTTP_201_CREATED)
async def create_server(data: MCPGatewayServerIn, db: DbSession, user: CurrentUser):
    _require_admin(user)
    exists = await db.scalar(
        select(MCPGatewayServer.id).where(MCPGatewayServer.name == data.name)
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"网关服务 {data.name} 已存在")
    await _validate_capability_binding(db, data.capability_id)
    row = MCPGatewayServer(
        name=data.name,
        description=data.description,
        transport=data.transport,
        url=data.url,
        headers=data.headers,
        command=data.command,
        args=data.args,
        env=data.env,
        cwd=data.cwd,
        api_token=data.api_token,
        capability_id=data.capability_id,
        enabled=data.enabled,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    return row


@router.put("/servers/{server_id}", response_model=MCPGatewayServerOut)
async def update_server(
    server_id: str, data: MCPGatewayServerIn, db: DbSession, user: CurrentUser
):
    _require_admin(user)
    row = await db.get(MCPGatewayServer, server_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "网关服务不存在")
    dup = await db.scalar(
        select(MCPGatewayServer.id).where(
            MCPGatewayServer.name == data.name, MCPGatewayServer.id != server_id
        )
    )
    if dup:
        raise HTTPException(status.HTTP_409_CONFLICT, f"网关服务 {data.name} 已存在")
    await _validate_capability_binding(db, data.capability_id, server_id)
    row.name = data.name
    row.description = data.description
    row.transport = data.transport
    row.url = data.url
    row.headers = data.headers
    row.command = data.command
    row.args = data.args
    row.env = data.env
    row.cwd = data.cwd
    row.api_token = data.api_token
    row.capability_id = data.capability_id
    row.enabled = data.enabled
    await db.commit()
    await db.refresh(row)
    return row


@router.delete("/servers/{server_id}", response_model=MessageOut)
async def delete_server(server_id: str, db: DbSession, user: CurrentUser):
    _require_admin(user)
    row = await db.get(MCPGatewayServer, server_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "网关服务不存在")
    await db.delete(row)
    await db.commit()
    return MessageOut(message=f"已删除网关服务 {row.name}")


@router.post("/servers/{server_id}/test", response_model=MCPGatewayTestOut)
async def test_server(server_id: str, db: DbSession, user: CurrentUser):
    """连接上游并列出工具，验证配置可用。"""
    _require_admin(user)
    row = await db.get(MCPGatewayServer, server_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "网关服务不存在")
    config = row_to_config(row)
    try:
        tools = await probe_tools(config)
    except Exception as exc:  # noqa: BLE001
        logger.exception("网关服务 %s 连接测试失败", row.name)
        return MCPGatewayTestOut(connected=False, error=str(exc)[:500])
    return MCPGatewayTestOut(
        connected=True,
        tools=[{"name": t["name"], "description": (t.get("description") or "")[:200]} for t in tools],
    )
