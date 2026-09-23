"""测试环境：强制使用内存 SQLite，避免污染开发数据库。"""

import os

# 必须在导入 app 之前强制覆盖（不可 setdefault：避免被本机环境变量带到文件库）
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ARTIFACT_STORAGE"] = "local"
os.environ["ARTIFACT_DIR"] = "./data/test-artifacts"
os.environ["JWT_SECRET"] = "test-secret-key-with-at-least-32-bytes!!"
# 开发态种子口令:显式提供非弱口令,避免 env 守卫随机生成导致登录用例不确定
os.environ["SEED_ADMIN_PASSWORD"] = "test-seed-admin-password-32-bytes!!"
os.environ["SEED_PUBLISHER_PASSWORD"] = "test-seed-publisher-password-32-bytes!!"
os.environ["SEED_USER_PASSWORD"] = "test-seed-user-password-32-bytes!!"

import asyncio

import httpx
import pytest

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, engine
from app.main import app

# mcp 2.x 保留了 mcp.server.fastmcp 的 shim 模块（find_spec 能查到，import 才报错），
# 因此必须真实 import 探测；本地 mcp>=2 时相关真实拉起服务的用例跳过（容器/CI 用 pinned mcp<2）。
try:  # noqa: SIM105
    from mcp.server.fastmcp import FastMCP as _FastMCP  # noqa: F401

    MCP_V1_AVAILABLE = True
except Exception:  # noqa: BLE001
    MCP_V1_AVAILABLE = False


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session", autouse=True)
async def _dispose_engine_at_end():
    """会话结束释放连接池：否则 aiosqlite 非守护线程会让 pytest 进程卡在退出。"""
    yield
    await engine.dispose()


@pytest.fixture
async def client():
    # 每个用例清空表，避免跨用例脏数据（同进程共用 :memory: StaticPool）
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
    transport = httpx.ASGITransport(app=app)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def _login(client, username: str, password: str) -> dict:
    r = await client.post("/api/auth/login", json={"username": username, "password": password})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


@pytest.fixture
async def admin_headers(client):
    return await _login(client, "admin", "test-seed-admin-password-32-bytes!!")


@pytest.fixture
async def publisher_headers(client):
    return await _login(client, "publisher", "test-seed-publisher-password-32-bytes!!")


@pytest.fixture
async def user_headers(client):
    return await _login(client, "user", "test-seed-user-password-32-bytes!!")
