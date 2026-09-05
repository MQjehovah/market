"""SSO/OIDC token 校验测试:用临时 RSA 自签 token + file:// JWKS 验证 verify_sso_token。

helper(make_rsa_key/write_jwks/sign_token/valid_claims/enable_sso)与
sso_env fixture、JWKS 缓存重置均在本文件内,保持独立可运行。

依赖:PyJWT(自签 RS256 token)、python-jose[cryptography](验签)。
"""

import json
import time

import jwt as pyjwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jose import jwk as jose_jwk

from app.config import get_settings
from app.core import sso_auth
from app.core.sso_auth import SsoAuthError, verify_sso_token

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

    def _reset():
        sso_auth._jwks_cache["data"] = None
        sso_auth._jwks_cache["fetched_at"] = 0.0
        sso_auth._jwks_cache["uri"] = ""

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
