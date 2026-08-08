"""异步 SQLAlchemy 引擎与会话管理。"""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from app.config import get_settings


class Base(DeclarativeBase):
    pass


settings = get_settings()
DATABASE_URL = settings.resolved_database_url()

engine_kwargs: dict = {"echo": settings.debug}
if DATABASE_URL.startswith("sqlite"):
    engine_kwargs["connect_args"] = {"check_same_thread": False}

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
