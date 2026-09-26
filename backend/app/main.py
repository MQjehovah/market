"""FastAPI 应用入口。"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal, init_db
from app.a2a.router import router as a2a_router
from app.a2a.router import well_known_router
from app.routers import (
    admin,
    agent_edit,
    assemble,
    auth,
    authoring,
    bindings,
    customize_edit,
    mcp_edit,
    my,
    portal,
    publish,
    runtime,
    skill_edit,
    tool_edit,
    workflows,
    mcp_gateway,
)
from app.models import MCPGatewayServer
from app.seed import seed_if_empty
from app.services.mcp_gateway import GatewayASGIApp, GatewayRegistry, row_to_config


async def _load_gateway_config(name: str) -> dict | None:
    async with SessionLocal() as db:
        row = await db.scalar(
            select(MCPGatewayServer).where(MCPGatewayServer.name == name)
        )
        return row_to_config(row) if row is not None else None


gateway_registry = GatewayRegistry(_load_gateway_config)
gateway_app = GatewayASGIApp(gateway_registry, _load_gateway_config)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await init_db()
    async with SessionLocal() as session:
        await seed_if_empty(session)
    try:
        yield
    finally:
        await gateway_registry.shutdown()


settings = get_settings()
app = FastAPI(
    title=settings.app_name,
    version=settings.app_version,
    description="企业AI能力平台 API：四大市场（Agent / 工具 / 技能 / MCP）、发布审核流程、版本管理、执行引擎。",
    lifespan=lifespan,
)
if (
    not settings.debug
    and settings.jwt_secret.startswith("dev-secret")
):
    import logging

    logging.getLogger("market").warning(
        "JWT_SECRET 仍为开发默认值，生产环境请通过环境变量覆盖"
    )

_cors = [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors or ["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(portal.router)
app.include_router(publish.router)
app.include_router(admin.router)
app.include_router(runtime.router)
app.include_router(assemble.router)
app.include_router(workflows.router)
app.include_router(bindings.router)
app.include_router(my.router)
app.include_router(agent_edit.router)
app.include_router(skill_edit.router)
app.include_router(tool_edit.router)
app.include_router(mcp_edit.router)
app.include_router(authoring.router)
app.include_router(customize_edit.rule_router)
app.include_router(customize_edit.command_router)
app.include_router(customize_edit.hook_router)
app.include_router(mcp_gateway.router)
app.include_router(a2a_router)
app.include_router(well_known_router)

# MCP HTTP 中转网关（SSE + Streamable HTTP），挂在 /api/mcp-gateway 下
app.mount("/api/mcp-gateway", gateway_app)


@app.get("/api")
async def root():
    return {
        "service": settings.app_name,
        "version": settings.app_version,
        "docs": "/docs",
    }


# 生产部署时若存在前端构建产物，则由后端直接托管。
# html=True 只处理目录 index，深链刷新（如 /capabilities/:id）仍会 404，需回退到 index.html。
frontend_dist = Path(__file__).resolve().parent.parent.parent / "frontend" / "dist"
if frontend_dist.is_dir():
    from starlette.exceptions import HTTPException as StarletteHTTPException
    from starlette.responses import Response
    from fastapi.staticfiles import StaticFiles

    class SPAStaticFiles(StaticFiles):
        async def get_response(self, path: str, scope) -> Response:
            try:
                return await super().get_response(path, scope)
            except StarletteHTTPException as exc:
                if exc.status_code != 404:
                    raise
                return await super().get_response("index.html", scope)

    app.mount("/", SPAStaticFiles(directory=frontend_dist, html=True), name="frontend")
