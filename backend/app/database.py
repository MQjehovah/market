"""异步 SQLAlchemy 引擎与会话管理。"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
DATABASE_URL = settings.resolved_database_url()

engine_kwargs: dict = {"echo": settings.debug}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}
    # :memory: 每个连接默认是独立库；StaticPool 保证同进程内共用一张内存库
    if ":memory:" in DATABASE_URL:
        engine_kwargs["poolclass"] = StaticPool

engine = create_async_engine(DATABASE_URL, **engine_kwargs)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db():
    async with SessionLocal() as session:
        yield session


async def init_db() -> None:
    """建表（生产环境建议使用迁移工具）。"""
    from app import models  # noqa: F401 确保模型已注册

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _auto_migrate(conn)


async def _auto_migrate(conn) -> None:
    """轻量增量迁移：为已存在的表补齐新增列（完整演进请用 Alembic）。"""
    from sqlalchemy import inspect

    def _do(sync_conn) -> None:
        inspector = inspect(sync_conn)
        if "capabilities" not in inspector.get_table_names():
            return
        cols = {c["name"] for c in inspector.get_columns("capabilities")}
        if "input_schema" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE capabilities ADD COLUMN input_schema JSON DEFAULT '{}'"
            )
        if "access_policy" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE capabilities ADD COLUMN access_policy VARCHAR(20) DEFAULT 'open'"
            )
        if "allowed_users" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE capabilities ADD COLUMN allowed_users JSON DEFAULT '[]'"
            )
        if "install_policy" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE capabilities ADD COLUMN install_policy VARCHAR(20) DEFAULT 'optional'"
            )
        if "changelog" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE capabilities ADD COLUMN changelog TEXT DEFAULT ''"
            )
        if "validation_report" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE capabilities ADD COLUMN validation_report JSON DEFAULT '{}'"
            )
        if "readme_md" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE capabilities ADD COLUMN readme_md TEXT DEFAULT ''"
            )
        if "distribution" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE capabilities ADD COLUMN distribution VARCHAR(16) DEFAULT 'both'"
            )
        if "risk_default" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE capabilities ADD COLUMN risk_default VARCHAR(20) DEFAULT 'read'"
            )
        if "data_domain" not in cols:
            sync_conn.exec_driver_sql(
                "ALTER TABLE capabilities ADD COLUMN data_domain VARCHAR(64) DEFAULT ''"
            )
        tables = inspector.get_table_names()
        if "usage_events" in tables:
            ue_cols = {c["name"] for c in inspector.get_columns("usage_events")}
            if "capability_version" not in ue_cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE usage_events ADD COLUMN capability_version VARCHAR(50) DEFAULT ''"
                )
            if "duration_ms" not in ue_cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE usage_events ADD COLUMN duration_ms INTEGER DEFAULT 0"
                )
            if "conversation_id" not in ue_cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE usage_events ADD COLUMN conversation_id VARCHAR(128) DEFAULT ''"
                )
            if "source" not in ue_cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE usage_events ADD COLUMN source VARCHAR(16) DEFAULT 'platform'"
                )
        if "user_capabilities" in tables:
            uc_cols = {c["name"] for c in inspector.get_columns("user_capabilities")}
            if "enabled" not in uc_cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE user_capabilities ADD COLUMN enabled BOOLEAN DEFAULT 1"
                )
        if "notifications" in tables:
            n_cols = {c["name"] for c in inspector.get_columns("notifications")}
            if "link" not in n_cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE notifications ADD COLUMN link VARCHAR(255) DEFAULT ''"
                )
        if "mcp_gateway_servers" in tables:
            mg_cols = {c["name"] for c in inspector.get_columns("mcp_gateway_servers")}
            if "capability_id" not in mg_cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE mcp_gateway_servers ADD COLUMN capability_id VARCHAR(36) DEFAULT ''"
                )
        if "users" in tables:
            user_cols = {c["name"] for c in inspector.get_columns("users")}
            if "department" not in user_cols:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE users ADD COLUMN department VARCHAR(100) DEFAULT ''"
                )

    await conn.run_sync(_do)
