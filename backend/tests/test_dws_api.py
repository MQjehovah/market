"""DWS 账号与登录 API。"""

from app.dws.device_flow import LoginSession
from app.dws.workspace import DwsWorkspace
from app.routers import dws as dws_router


class _FakeManager:
    async def start(self, user_id: str) -> LoginSession:
        s = LoginSession(session_id="s-1", user_id=user_id)
        s.user_code = "ABCD-EFGH"
        s.verification_url = "https://login.dingtalk.com/oauth2/device/verify.htm?user_code=ABCD-EFGH"
        return s

    async def poll(self, user_id: str, session_id: str):
        return None

    async def cancel(self, user_id: str):
        return None


async def test_workspace_requires_auth(client):
    r = await client.get("/api/dws/workspace")
    assert r.status_code == 401


async def test_workspace_unauthorized_shape(client, user_headers, tmp_path, monkeypatch):
    monkeypatch.setattr(dws_router, "_workspace", DwsWorkspace(tmp_path))
    r = await client.get("/api/dws/workspace", headers=user_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["authorized"] is False
    assert body["status"] == "unauthorized"
    assert body["user_id"]
    assert body["page_url"]


async def test_login_start_returns_code(client, user_headers, tmp_path, monkeypatch):
    monkeypatch.setattr(dws_router, "_workspace", DwsWorkspace(tmp_path))
    monkeypatch.setattr(dws_router, "manager", lambda: _FakeManager())

    r = await client.post("/api/dws/login/start", headers=user_headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["user_code"] == "ABCD-EFGH"
    assert body["session_id"] == "s-1"
    assert body["status"] == "authorizing"
