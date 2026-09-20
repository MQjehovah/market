"""测试环境：强制使用内存 SQLite，避免污染开发数据库。"""

import os

# 必须在导入 app 之前强制覆盖（不可 setdefault：避免被本机环境变量带到文件库）
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["ARTIFACT_STORAGE"] = "local"
os.environ["ARTIFACT_DIR"] = "./data/test-artifacts"
os.environ["JWT_SECRET"] = "test-secret-key-with-at-least-32-bytes!!"

import asyncio

import httpx
import pytest

from app.config import get_settings

get_settings.cache_clear()

from app.database import Base, engine
from app.main import app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


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
    return await _login(client, "admin", "admin123")


@pytest.fixture
async def publisher_headers(client):
    return await _login(client, "publisher", "publisher123")


@pytest.fixture
async def user_headers(client):
    return await _login(client, "user", "user123456")
