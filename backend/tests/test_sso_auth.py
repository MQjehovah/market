"""SSO/OIDC token 校验测试:用临时 RSA 自签 token + file:// JWKS 验证 verify_sso_token。

helper(make_rsa_key/write_jwks/sign_token/valid_claims/enable_sso)与
sso_env fixture、JWKS 缓存重置均在本文件内,保持独立可运行。

依赖:PyJWT(自签 RS256 token)、python-jose[cryptography](验签)。

后半部分是 get_current_user 双轨(HS256 | SSO)测试:复用本文件的 sso_env 签发
RS256 token,建临时 sqlite 异步库 + db fixture 直调鉴权函数,隔离真实 DB。
"""

import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi import HTTPException
from jose import jwk as jose_jwk
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.auth import create_access_token, hash_password, get_current_user, get_current_user_optional
from app.config import get_settings
from app.core import sso_auth
from app.core.sso_auth import SsoAuthError, verify_sso_token
from app.database import Base
from app.models import User

ISSUER = "https://sso.example.com"
AUDIENCE = "market-dashboard"


def make_rsa_key(kid="k1"):
    """生成 RSA2048 私钥,返回 (私钥, 公钥 JWK dict)。"""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public_jwk = jose_jwk.RSAKey(key.public_key(), algorithm="RS256").to_dict()
    public_jwk.update({"kid": kid, "use": "sig", "alg": "RS256"})
    return key, public_jwk


def write_jwks(tmp_path, *public_jwks):
    """把若干公钥 JWK 写成临时 JWKS 文件,返回其 file:// URI。"""
    path = tmp_path / "jwks.json"
    path.write_text(json.dumps({"keys": list(public_jwks)}), encoding="utf-8")
    return path.as_uri()


def sign_token(payload, key, kid="k1"):
    """用 pyjwt 以 RS256 自签 token。"""
    headers = {"kid": kid} if kid else None
    return pyjwt.encode(payload, key, algorithm="RS256", headers=headers)


def valid_claims(**overrides):
    now = int(time.time())
    claims = {
        "sub": "10086",
        "iss": ISSUER,
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 3600,
    }
    claims.update(overrides)
    return claims


def enable_sso(monkeypatch, jwks_uri, issuer=ISSUER, audience=AUDIENCE):
    """monkeypatch settings 打开 SSO(settings 是缓存单例 pydantic 对象,可改属性)。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "sso_issuer", issuer)
    monkeypatch.setattr(settings, "sso_audience", audience)
    monkeypatch.setattr(settings, "sso_jwks_uri", jwks_uri)


@pytest.fixture(autouse=True)
def _cleanup_sso_settings_and_cache(monkeypatch):
    """每个测试前后重置 SSO settings 与 sso_auth 的 JWKS 模块级缓存。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "sso_issuer", "")
    monkeypatch.setattr(settings, "sso_audience", "")
    monkeypatch.setattr(settings, "sso_jwks_uri", "")
    monkeypatch.setattr(settings, "sso_client_id", "market")
    monkeypatch.setattr(settings, "sso_client_secret", "")
    monkeypatch.setattr(settings, "sso_redirect_uri", "")
    monkeypatch.setattr(settings, "sso_redirect_target", "/login")

    def _reset():
        sso_auth._jwks_cache["data"] = None
        sso_auth._jwks_cache["fetched_at"] = 0.0
        sso_auth._jwks_cache["uri"] = ""
        sso_auth._states.clear()

    _reset()
    yield
    _reset()


@pytest.fixture
def sso_env(monkeypatch, tmp_path):
    """生成密钥+JWKS 并打开 SSO 配置,返回 (私钥, 公钥 JWK dict)。"""
    key, public_jwk = make_rsa_key()
    jwks_uri = write_jwks(tmp_path, public_jwk)
    enable_sso(monkeypatch, jwks_uri)
    return key, public_jwk


def test_verify_sso_token_accepts_valid_token(sso_env):
    key, _ = sso_env
    token = sign_token(valid_claims(), key)
    claims = verify_sso_token(token)
    assert claims["sub"] == "10086"
    assert claims["iss"] == ISSUER


def test_verify_sso_token_rejects_bad_signature(sso_env):
    key, _ = sso_env
    token = sign_token(valid_claims(), key)
    with pytest.raises(SsoAuthError):
        verify_sso_token(token + "tampered")


def test_verify_sso_token_rejects_wrong_audience(sso_env):
    key, _ = sso_env
    token = sign_token(valid_claims(aud="some-other-app"), key)
    with pytest.raises(SsoAuthError):
        verify_sso_token(token)


def test_verify_sso_token_rejects_expired_token(sso_env):
    key, _ = sso_env
    token = sign_token(valid_claims(exp=int(time.time()) - 60), key)
    with pytest.raises(SsoAuthError):
        verify_sso_token(token)


def test_verify_sso_token_disabled_when_issuer_empty():
    token = "not.even.a.jwt"
    with pytest.raises(SsoAuthError, match="[Nn]ot configured"):
        verify_sso_token(token)


def test_verify_sso_token_falls_back_to_first_key_without_kid(sso_env):
    key, _ = sso_env
    token = sign_token(valid_claims(), key, kid=None)
    assert verify_sso_token(token)["sub"] == "10086"


def test_verify_sso_token_selects_key_by_kid(monkeypatch, tmp_path):
    key1, jwk1 = make_rsa_key(kid="k1")
    key2, jwk2 = make_rsa_key(kid="k2")
    enable_sso(monkeypatch, write_jwks(tmp_path, jwk1, jwk2))
    token = sign_token(valid_claims(), key2, kid="k2")
    assert verify_sso_token(token)["sub"] == "10086"


def test_verify_sso_token_rejects_unknown_kid(monkeypatch, tmp_path):
    key1, jwk1 = make_rsa_key(kid="k1")
    _, jwk2 = make_rsa_key(kid="k2")
    enable_sso(monkeypatch, write_jwks(tmp_path, jwk1, jwk2))
    token = sign_token(valid_claims(), key1, kid="ghost")
    with pytest.raises(SsoAuthError):
        verify_sso_token(token)


# --------------------------------------------------------------------------
# get_current_user 双轨鉴权（HS256 | SSO）测试
# --------------------------------------------------------------------------

SSO_EMP_NO = "10086"


@pytest.fixture
async def db(tmp_path):
    """每测试一个独立 sqlite 文件库 + 单 async session（隔离真实 DB）。"""
    db_path = tmp_path / "test_sso_auth_users.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path.as_posix()}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    session = maker()
    try:
        yield session
    finally:
        await session.close()
        await engine.dispose()


def _sso_employee_token(key, emp_no=SSO_EMP_NO, name=None, email=None):
    claims = {"sub": emp_no}
    if name is not None:
        claims["name"] = name
    if email is not None:
        claims["email"] = email
    return sign_token(valid_claims(**claims), key)


async def _count_users(db) -> int:
    return (await db.scalar(select(func.count()).select_from(User))) or 0


async def test_get_current_user_sso_provisions_and_does_not_duplicate(sso_env, db):
    """SSO 合法 token + 用户不存在 -> 自动建号返回该 User（username=工号）；
    再次调用不重复建号（行数不变）。"""
    key, _ = sso_env
    token = _sso_employee_token(key, name="测试员工", email="10086@example.com")

    user = await get_current_user(token, db)

    assert user.username == SSO_EMP_NO
    assert user.email == "10086@example.com"
    assert user.display_name == "测试员工"
    assert user.role == "user"
    assert user.is_active is True
    assert await _count_users(db) == 1

    again = await get_current_user(token, db)
    assert again.id == user.id
    assert await _count_users(db) == 1


async def test_get_current_user_sso_matches_existing_account_by_email(sso_env, db):
    """SSO token 的邮箱命中系统自建账号时复用该账号(不新建、不改 username)。

    与网关控制台一致: 邮箱是首选唯一标识, 避免同一人两份账号。
    """
    key, _ = sso_env
    # 系统自建账号(如本地注册): username 不是工号, 但邮箱与 SSO 一致
    existing = User(
        id="u-local",
        username="jimingqing",
        email="jimingqing@xzrobot.com",
        password_hash=hash_password("not-used-sso"),
        display_name="旧名字",
        role="user",
        is_active=True,
    )
    db.add(existing)
    await db.commit()

    token = _sso_employee_token(
        key, emp_no="202202100024", name="季明清", email="jimingqing@xzrobot.com"
    )
    user = await get_current_user(token, db)

    assert user.id == "u-local"

    assert user.username == "jimingqing"  # username 不在 SSO 身份键上, 保持不变
    assert user.display_name == "季明清"  # 姓名以 SSO 为权威源刷新
    assert await _count_users(db) == 1


async def test_get_current_user_sso_without_email_uses_derived_fallback(sso_env, db):
    """claims 缺 email 时用派生自唯一 username 的占位邮箱，name 仍取自 claims。"""
    key, _ = sso_env
    token = _sso_employee_token(key, emp_no="20001", name="无名氏")

    user = await get_current_user(token, db)

    assert user.username == "20001"
    assert user.email == "20001@sso.local"
    assert user.display_name == "无名氏"


async def test_get_current_user_sso_rejects_invalid_token(sso_env, db):
    """双轨坏 token：无效字符串 -> 401（与轨1同 detail 风格）。"""
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user("not.a.valid.jwt", db)
    assert exc_info.value.status_code == 401


async def test_get_current_user_sso_disabled_user_forbidden(sso_env, db):
    """SSO 命中已存在但被禁用的用户 -> 403。"""
    key, _ = sso_env
    disabled = User(
        username="10087",
        email="10087@example.com",
        password_hash="unused",
        display_name="已禁用",
        role="user",
        is_active=False,
    )
    db.add(disabled)
    await db.commit()

    token = _sso_employee_token(key, emp_no="10087")
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(token, db)
    assert exc_info.value.status_code == 403


async def test_get_current_user_hs256_legacy_token_still_works(db):
    """HS256 老轨回归：市场登录下发的 token 仍按原逻辑通过。"""
    user = User(
        username="legacy",
        email="legacy@example.com",
        password_hash="unused",
        display_name="老用户",
        role="user",
        is_active=True,
    )
    db.add(user)
    await db.commit()

    token = create_access_token(user)
    got = await get_current_user(token, db)

    assert got.id == user.id
    assert got.username == "legacy"


async def test_get_current_user_hs256_disabled_user_unauthorized(db):
    """HS256 老轨回归：命中已禁用用户 -> 401（与旧语义一致，非 403）。"""
    user = User(
        username="legacy_disabled",
        email="legacy_disabled@example.com",
        password_hash="unused",
        role="user",
        is_active=False,
    )
    db.add(user)
    await db.commit()

    token = create_access_token(user)
    with pytest.raises(HTTPException) as exc_info:
        await get_current_user(token, db)
    assert exc_info.value.status_code == 401


async def test_get_current_user_optional_invalid_token_returns_none(sso_env, db):
    """get_current_user_optional：坏 token / 缺 token -> None（而非抛）。"""
    assert await get_current_user_optional("not.a.valid.jwt", db) is None
    assert await get_current_user_optional(None, db) is None


async def test_get_current_user_optional_valid_sso_token_returns_user(sso_env, db):
    """get_current_user_optional：合法 SSO token 同样走 SSO 轨自动建号。"""
    key, _ = sso_env
    token = _sso_employee_token(key, emp_no="30001", name="可选用户")

    user = await get_current_user_optional(token, db)

    assert user is not None
    assert user.username == "30001"
    assert user.display_name == "可选用户"


async def test_sso_start_disabled_returns_404(client):
    r = await client.get("/api/auth/sso/start", follow_redirects=False)
    assert r.status_code == 404


async def test_sso_start_redirects_when_configured(sso_env, client, monkeypatch):
    # sso_env 已打开 issuer;补登录跳转配置
    settings = get_settings()
    monkeypatch.setattr(settings, "sso_redirect_uri", "http://127.0.0.1:8000/api/auth/oidc/callback")
    r = await client.get("/api/auth/sso/start", follow_redirects=False)
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith(ISSUER + "/authorize?")
    assert "state=" in loc
    assert "client_id=market" in loc


async def test_oidc_callback_rejects_bad_state(sso_env, client, monkeypatch):
    """state 失效时跳回登录页并带 error,不把 JSON 错误丢给浏览器。"""
    settings = get_settings()
    monkeypatch.setattr(settings, "sso_redirect_uri", "http://127.0.0.1:8000/api/auth/oidc/callback")
    monkeypatch.setattr(settings, "sso_redirect_target", "/market/login")
    r = await client.get(
        "/api/auth/oidc/callback",
        params={"code": "abc", "state": "bad"},
        follow_redirects=False,
    )
    assert r.status_code == 302
    loc = r.headers["location"]
    assert loc.startswith("/market/login?error=")
    assert "sso_token=" not in loc


async def test_oidc_callback_missing_params_redirects(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "sso_redirect_uri", "http://127.0.0.1:8000/api/auth/oidc/callback")
    monkeypatch.setattr(settings, "sso_redirect_target", "/market/login")
    r = await client.get("/api/auth/oidc/callback", follow_redirects=False)
    assert r.status_code == 302
    assert "error=" in r.headers["location"]


async def test_oidc_callback_disabled_redirects(client, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "sso_issuer", "")
    monkeypatch.setattr(settings, "sso_client_id", "")
    monkeypatch.setattr(settings, "sso_redirect_uri", "")
    r = await client.get(
        "/api/auth/oidc/callback", params={"code": "a", "state": "b"}, follow_redirects=False
    )
    assert r.status_code == 302
    assert "error=" in r.headers["location"]


async def test_http_me_accepts_sso_token(sso_env, client):
    """HTTP 层:SSO access/id token 作为 Bearer 可打 /api/auth/me 并自动建号。"""
    key, _ = sso_env
    token = _sso_employee_token(key, emp_no="10086", name="测试员工", email="10086@example.com")
    r = await client.get("/api/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["username"] == "10086"
    assert body["display_name"] == "测试员工"
    assert body["role"] == "user"
