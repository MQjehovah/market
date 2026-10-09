"""试用走个人网关密钥：登录时换取，没有密钥时不返回模拟回答。"""

import pytest

from app.config import get_settings
from app.models import User
from app.services import router_key
from app.services.secret_vault import decrypt_value
from test_workflow import _agent_zip, _publish_capability


class _Resp:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, *args, **kwargs):
        self.posts = []
        self.gets = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def post(self, url, data=None, headers=None):
        self.posts.append((url, data))
        return _Resp(200, {"access_token": "router-access"})

    async def get(self, url, headers=None):
        self.gets.append((url, headers))
        return _Resp(200, {"key": "sk-person"})


@pytest.mark.asyncio
async def test_capture_router_key_exchanges_id_token(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "sso_issuer", "http://sso.example")
    monkeypatch.setattr(settings, "router_admin_url", "http://router.example/router")
    monkeypatch.setattr(settings, "sso_client_id", "market")
    monkeypatch.setattr(settings, "sso_client_secret", "secret")
    holder = {}

    def factory(*args, **kwargs):
        client = _Client()
        holder["client"] = client
        return client

    monkeypatch.setattr(router_key.httpx, "AsyncClient", factory)
    key = await router_key.capture_router_key("id-token-value")
    assert key == "sk-person"
    url, form = holder["client"].posts[0]
    assert url == "http://sso.example/token"
    assert form["grant_type"] == "urn:ietf:params:oauth:grant-type:token-exchange"
    assert form["subject_token"] == "id-token-value"
    assert form["audience"] == "router"
    assert form["client_id"] == "market"
    assert form["client_secret"] == "secret"
    get_url, headers = holder["client"].gets[0]
    assert get_url == "http://router.example/router/api/me/key"
    assert headers["Authorization"] == "Bearer router-access"


@pytest.mark.asyncio
async def test_capture_skips_when_router_admin_unset(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "sso_issuer", "http://sso.example")
    monkeypatch.setattr(settings, "router_admin_url", "")
    called = {"n": 0}

    def factory(*args, **kwargs):
        called["n"] += 1
        return _Client()

    monkeypatch.setattr(router_key.httpx, "AsyncClient", factory)
    assert await router_key.capture_router_key("id-token-value") == ""
    assert called["n"] == 0


def test_personal_key_roundtrip_and_missing_gateway():
    settings = get_settings()
    user = User(
        id="u1",
        username="10086",
        email="a@b.c",
        password_hash="x",
        role="user",
    )
    assert router_key.personal_gateway_credentials(user) is None
    assert "企业统一登录" in router_key.trial_unavailable_detail(user)

    router_key.store_router_key(user, "sk-person")
    assert decrypt_value(user.router_key_ciphertext) == "sk-person"
    assert user.router_key_ciphertext != "sk-person"

    original_base = settings.llm_base_url
    original_model = settings.llm_model
    original_router = settings.router_base_url
    try:
        settings.llm_base_url = ""
        settings.router_base_url = ""
        settings.llm_model = "deepseek-flash"
        assert router_key.personal_gateway_credentials(user) is None
        assert router_key.trial_unavailable_detail(user) == router_key.TRIAL_NEEDS_GATEWAY

        settings.router_base_url = "http://gw.example/v1/"
        base, key, model = router_key.personal_gateway_credentials(user)
        assert base == "http://gw.example/v1"
        assert key == "sk-person"
        assert model == "deepseek-flash"
    finally:
        settings.llm_base_url = original_base
        settings.llm_model = original_model
        settings.router_base_url = original_router


@pytest.mark.asyncio
async def test_trial_without_personal_key_is_not_simulated(
    client, publisher_headers, admin_headers
):
    persona = "trial-no-key"
    await _publish_capability(
        client, publisher_headers, admin_headers, persona, "agent", _agent_zip(persona)
    )
    r = await client.post(
        f"/api/runtime/agents/{persona}/tasks",
        headers=admin_headers,
        json={"task": "打个招呼"},
    )
    assert r.status_code == 503, r.text
    assert "企业统一登录" in r.text
    assert "模拟" not in r.text
