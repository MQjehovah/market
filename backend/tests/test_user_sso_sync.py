"""统一用户体系: users 表启动幂等迁移测试。"""

from app.database import run_user_column_migrations


async def test_user_column_migrations_noop_on_sqlite():
    # 测试库为 SQLite: 迁移应跳过(PG 专用), 且不抛异常
    await run_user_column_migrations()
