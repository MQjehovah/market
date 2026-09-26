"""SSO/OIDC token 校验:从 issuer 的 JWKS 拉取公钥验 RS256 签名(带缓存)。

与 app.auth(自家 HS256 token)相互独立。本模块专验 SSO 签发的 RS256 token,
通过则返回 claims,其中 sub 视为工号,供后续 D-ready 的 get_current_user
接入使用。未配置 sso_issuer 时按 SSO 禁用处理。

另提供授权码登录:build_authorize_url / exchange_code / state 一次性校验。
"""

import base64
import hashlib
import hmac
import json
import secrets
import threading
import time
import urllib.parse
import urllib.request
from urllib.parse import urlparse
from urllib.request import url2pathname

from jose import exceptions as jose_exceptions
from jose import jwk as jose_jwk
from jose import jwt as jose_jwt
from jose.exceptions import JOSEError

from app.config import get_settings

ALGORITHM = "RS256"

JWKS_CACHE_TTL_SECONDS = 300
STATE_TTL_SECONDS = 300


class SsoAuthError(Exception):
    """SSO token 校验失败(未配置、JWKS 拉取失败或签名/声明无效)。"""


# 模块级 JWKS 缓存:同一 sso_jwks_uri 在 TTL 内只拉一次,简单锁防并发重复拉取
_jwks_cache = {"uri": "", "data": None, "fetched_at": 0.0}
_jwks_lock = threading.Lock()


def is_configured() -> bool:
    """仅配置了 issuer 即可验 SSO token;登录跳转还需 client/redirect。"""
    return bool(get_settings().sso_issuer)


def is_login_configured() -> bool:
    settings = get_settings()
    return bool(settings.sso_issuer and settings.sso_client_id and settings.sso_redirect_uri)


def _resolve_jwks_uri() -> str:
    """JWKS 地址:显式配置优先,无则由 issuer 推断。"""
    settings = get_settings()
    uri = (settings.sso_jwks_uri or "").strip()
    if uri:
        return uri
    issuer = (settings.sso_issuer or "").strip()
    if issuer:
        return issuer.rstrip("/") + "/.well-known/jwks.json"
    return ""


def _read_jwks(uri: str) -> dict:
    """从 uri 读取 JWKS 文档,支持 http(s) 与 file:// 两种 scheme。"""
    scheme = urlparse(uri).scheme
    try:
        if scheme in ("http", "https"):
            with urllib.request.urlopen(uri, timeout=10) as resp:
                return json.loads(resp.read().decode("utf-8"))
        if scheme == "file":
            path = url2pathname(urlparse(uri).path)
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except (OSError, ValueError) as e:
        raise SsoAuthError(f"拉取 JWKS 失败({uri}): {e}") from e
    raise SsoAuthError(f"不支持的 JWKS URI scheme: {scheme!r}")


def _get_jwks() -> dict:
    """带缓存的 JWKS 获取:TTL 内命中缓存,过期或换 URI 时加锁重拉。"""
    uri = _resolve_jwks_uri()
    if not uri:
        raise SsoAuthError("SSO not configured")
    cache = _jwks_cache
    now = time.time()
    if (
        cache["uri"] == uri
        and cache["data"] is not None
        and now - cache["fetched_at"] < JWKS_CACHE_TTL_SECONDS
    ):
        return cache["data"]
    with _jwks_lock:
        if (
            cache["uri"] == uri
            and cache["data"] is not None
            and time.time() - cache["fetched_at"] < JWKS_CACHE_TTL_SECONDS
        ):
            return cache["data"]
        data = _read_jwks(uri)
        cache["uri"] = uri
        cache["data"] = data
        cache["fetched_at"] = time.time()
        return data


def _find_key(kid: str | None):
    """在 JWKS keys 中定位公钥:按 kid 匹配,找不到返回 None;无 kid 取第一把兜底。"""
    keys = _get_jwks().get("keys") or []
    if not keys:
        raise SsoAuthError("JWKS 文档缺少 keys")
    if not kid:
        return keys[0]
    for item in keys:
        if item.get("kid") == kid:
            return item
    return None


def _invalidate_jwks_cache() -> None:
    """清空 JWKS 缓存,下次 _get_jwks 强制重拉。"""
    _jwks_cache["uri"] = ""
    _jwks_cache["data"] = None
    _jwks_cache["fetched_at"] = 0.0


def _public_key(kid: str | None):
    """把 JWK dict 构造成可验签的公钥对象(cryptography RSAKey)。

    kid 未命中时清缓存重拉一次兜底(防 IdP 轮换密钥在 TTL 内导致全线 401),
    重拉后仍无匹配则抛 SsoAuthError。
    """
    jwk_dict = _find_key(kid)
    if jwk_dict is None:
        _invalidate_jwks_cache()
        jwk_dict = _find_key(kid)
    if jwk_dict is None:
        raise SsoAuthError("找不到匹配的签名密钥")
    return jose_jwk.construct(jwk_dict, algorithm=ALGORITHM)


def verify_sso_token(token: str, audience: str | None = None) -> dict:
    """校验 SSO 签发的 RS256 token,通过则返回 claims(含 sub 工号)。

    未配置 sso_issuer 视为 SSO 禁用;**audience 强制**:显式 audience 或 sso_audience
    二者必有一,否则一律拒绝(fail-closed,避免接受未面向本资源的 token)。
    iss/aud 不匹配、签名无效、过期、缺少 sub(工号)等一律抛 SsoAuthError。
    """
    settings = get_settings()
    if not settings.sso_issuer:
        raise SsoAuthError("SSO not configured")
    expected_aud = (audience or settings.sso_audience or "").strip()
    if not expected_aud:
        raise SsoAuthError("SSO audience 未配置, 拒绝校验(需设置 sso_audience 或显式 audience)")
    try:
        header = jose_jwt.get_unverified_header(token)
    except jose_exceptions.JWTError as e:
        raise SsoAuthError(f"SSO token header 无效: {e}") from e
    kid = header.get("kid")
    try:
        key = _public_key(kid)
        claims = jose_jwt.decode(
            token,
            key,
            algorithms=[ALGORITHM],
            issuer=settings.sso_issuer,
            audience=expected_aud,
        )
    except SsoAuthError:
        raise
    except JOSEError as e:
        # JOSEError 覆盖 JWTError 及其兄弟类 JWKError(公钥构造失败),
        # 单捕 JWTError 会让 JWKError 裸抛 500
        raise SsoAuthError(f"SSO token 无效: {e}") from e
    if not claims.get("sub"):
        raise SsoAuthError("SSO token 缺少 sub(工号)")
    return claims


def build_authorize_url(state: str) -> str:
    """构造 SSO OIDC 授权跳转 URL(code;PKCE 由 SSO 端按需处理)。"""
    settings = get_settings()
    issuer = (settings.sso_issuer or "").strip()
    if not issuer:
        raise SsoAuthError("SSO not configured")
    params = {
        "response_type": "code",
        "client_id": settings.sso_client_id,
        "redirect_uri": settings.sso_redirect_uri,
        "state": state,
        "scope": "openid profile",
    }
    return issuer.rstrip("/") + "/authorize?" + urllib.parse.urlencode(params)


def exchange_code(code: str) -> str:
    """authorization_code → token 交换,返回 id_token 字符串。"""
    settings = get_settings()
    issuer = (settings.sso_issuer or "").strip()
    if not issuer:
        raise SsoAuthError("SSO not configured")
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": settings.sso_redirect_uri,
        "client_id": settings.sso_client_id,
    }
    secret = settings.sso_client_secret
    if secret:
        form["client_secret"] = secret
    data = urllib.parse.urlencode(form).encode("utf-8")
    req = urllib.request.Request(issuer.rstrip("/") + "/token", data=data, method="POST")
    if secret:
        basic = base64.b64encode(f"{settings.sso_client_id}:{secret}".encode()).decode("ascii")
        req.add_header("Authorization", "Basic " + basic)
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except (OSError, ValueError) as e:
        raise SsoAuthError(f"SSO token 交换失败: {e}") from e
    id_token = payload.get("id_token")
    if not id_token:
        raise SsoAuthError("SSO token 响应缺少 id_token")
    return id_token


def safe_next_path(raw: str | None) -> str:
    """只保留站内相对路径。外链、协议相对地址和登录页本身都丢掉。"""
    if not raw:
        return ""
    value = str(raw).strip()
    if not value or len(value) > 512:
        return ""
    if not value.startswith("/") or value.startswith("//") or value.startswith("/\\"):
        return ""
    if "\\" in value or "://" in value or any(ord(ch) < 32 for ch in value):
        return ""
    path = value.split("?", 1)[0].split("#", 1)[0]
    if path == "/login" or path.startswith("/login/"):
        return ""
    return value


def _state_secret() -> bytes:
    """state 签名密钥：复用 JWT_SECRET（多 worker 同值 → 无进程内状态）。"""
    from app.config import get_settings

    secret = getattr(get_settings(), "jwt_secret", "") or ""
    return (secret or "market-sso-state-fallback").encode("utf-8")


def new_state(next_path: str = "") -> str:
    """生成**无状态**签名 state（含回跳路径+时间戳+HMAC）。

    多 worker 部署下不再依赖进程内字典：任意 worker 都能校验，杜绝
    「SSO 登录状态已失效」的随机失败。
    """
    nxt = safe_next_path(next_path)
    b64 = base64.urlsafe_b64encode(nxt.encode("utf-8")).decode("ascii").rstrip("=")
    body = f"{int(time.time())}.{secrets.token_urlsafe(8)}.{b64}"
    sig = hmac.new(_state_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    return f"{body}.{sig}"


def consume_state(state: str) -> str | None:
    """校验无状态签名 state。失效返回 None；成功返回站内回跳路径（可为空串）。"""
    if not state or not isinstance(state, str):
        return None
    parts = state.split(".")
    if len(parts) != 4:
        return None
    ts_s, nonce, b64, sig = parts
    body = f"{ts_s}.{nonce}.{b64}"
    expected = hmac.new(_state_secret(), body.encode("ascii"), hashlib.sha256).hexdigest()[:32]
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        ts = int(ts_s)
    except ValueError:
        return None
    if (time.time() - ts) > STATE_TTL_SECONDS:
        return None
    try:
        pad = "=" * (-len(b64) % 4)
        return base64.urlsafe_b64decode(b64 + pad).decode("utf-8")
    except Exception:  # noqa: BLE001
        return ""


def validate_state(state: str) -> bool:
    """校验 state:签名非法/过期返回 False。"""
    return consume_state(state) is not None
