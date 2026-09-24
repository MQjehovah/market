"""用户 department 字段：老库增量迁移测试（启动自愈 + 幂等）。"""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.database import _auto_migrate

# capabilities 表存在时 _auto_migrate 才会继续执行后续表迁移（与真实老库一致）
LEGACY_CAPABILITIES_DDL = "CREATE TABLE capabilities (id VARCHAR(36) NOT NULL PRIMARY KEY)"

LEGACY_USERS_DDL = """
CREATE TABLE users (
    id VARCHAR(36) NOT NULL PRIMARY KEY,
    username VARCHAR(64) NOT NULL,
    email VARCHAR(255) NOT NULL,
    password_hash VARCHAR(255) NOT NULL,
    display_name VARCHAR(64),
    role VARCHAR(16),
    organization VARCHAR(100),
    team VARCHAR(100),
    is_active BOOLEAN,
    created_at DATETIME
)
"""


async def _column_names(conn, table: str) -> set[str]:
    result = await conn.execute(text(f"PRAGMA table_info({table})"))
    return {row[1] for row in result.fetchall()}


@pytest.fixture
async def legacy_engine(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{(tmp_path / 'legacy.db').as_posix()}")
    try:
        yield engine
    finally:
        await engine.dispose()


async def test_auto_migrate_adds_department_and_backfills(legacy_engine):
    """老库 users 表缺 department：迁移补列，存量行回填空串；重复执行不报错。"""
    async with legacy_engine.begin() as conn:
        await conn.execute(text(LEGACY_CAPABILITIES_DDL))
        await conn.execute(text(LEGACY_USERS_DDL))
        await conn.execute(
            text(
                "INSERT INTO users (id, username, email, password_hash, display_name, role, "
                "organization, team, is_active) VALUES "
                "('u1', 'legacy', 'legacy@example.com', 'x', '老用户', 'user', '', '', 1)"
            )
        )
        assert "department" not in await _column_names(conn, "users")

        await _auto_migrate(conn)
        assert "department" in await _column_names(conn, "users")
        value = await conn.scalar(text("SELECT department FROM users WHERE id = 'u1'"))
        assert value == ""

        # 幂等：重复迁移不报错，列与存量数据保持
        await _auto_migrate(conn)
        assert "department" in await _column_names(conn, "users")
        assert await conn.scalar(text("SELECT department FROM users WHERE id = 'u1'")) == ""
