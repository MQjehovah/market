"""测试环境：使用内存 SQLite，避免污染开发数据库。"""

import os

os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
os.environ.setdefault("ARTIFACT_STORAGE", "local")
os.environ.setdefault("ARTIFACT_DIR", "./data/test-artifacts")
os.environ.setdefault("JWT_SECRET", "test-secret-key-with-at-least-32-bytes!!")

import asyncio

import httpx
import pytest
from app.main import app


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
async def client():
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
