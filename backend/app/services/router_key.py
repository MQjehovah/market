"""企业登录时换取并保存该员工的网关密钥，与桌面端同一套。"""

from __future__ import annotations

import logging

import httpx

from app.config import get_settings
from app.models import User
from app.services.secret_vault import decrypt_value, encrypt_value

logger = logging.getLogger(__name__)

_EXCHANGE = "urn:ietf:params:oauth:grant-type:token-exchange"
_ID_TOKEN = "urn:ietf:params:oauth:token-type:id_token"

TRIAL_NEEDS_SSO = "试用记在你自己的模型额度上。请退出后用企业统一登录再进来。"
TRIAL_NEEDS_GATEWAY = "试用还没接上模型网关。"


async def capture_router_key(id_token: str) -> str:
    """用还在手里的 id_token 换 router 令牌，再取个人 sk-。失败返回空串，不打断登录。"""
    settings = get_settings()
    issuer = (settings.sso_issuer or "").strip().rstrip("/")
    admin = (settings.router_admin_url or "").strip().rstrip("/")
    if not issuer or not admin or not (id_token or "").strip():
        return ""
    form = {
        "grant_type": _EXCHANGE,
        "subject_token": id_token,
        "subject_token_type": _ID_TOKEN,
        "audience": "router",
        "client_id": settings.sso_client_id,
    }
    if settings.sso_client_secret:
        form["client_secret"] = settings.sso_client_secret
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            token_resp = await client.post(f"{issuer}/token", data=form)
            if token_resp.status_code >= 400:
                logger.warning("换取模型网关令牌失败: HTTP %s", token_resp.status_code)
                return ""
            access = str((token_resp.json() or {}).get("access_token") or "").strip()
            if not access:
                logger.warning("换取模型网关令牌失败: 响应没有 access_token")
                return ""
            key_resp = await client.get(
                f"{admin}/api/me/key",
                headers={"Authorization": f"Bearer {access}"},
            )
            if key_resp.status_code >= 400:
                logger.warning("读取个人模型密钥失败: HTTP %s", key_resp.status_code)
                return ""
            return str((key_resp.json() or {}).get("key") or "").strip()
    except (httpx.HTTPError, ValueError, TypeError) as exc:
        logger.warning("读取个人模型密钥失败: %s", type(exc).__name__)
        return ""


def store_router_key(user: User, plaintext: str) -> None:
    user.router_key_ciphertext = encrypt_value(plaintext)


def personal_gateway_credentials(user: User) -> tuple[str, str, str] | None:
    """试用要用的 (网关根地址, 个人密钥, 模型名)。缺任何一项就返回 None。"""
    settings = get_settings()
    base = (settings.router_base_url or settings.llm_base_url or "").strip().rstrip("/")
    model = (settings.llm_model or "").strip()
    cipher = (getattr(user, "router_key_ciphertext", None) or "").strip()
    if not cipher or not base or not model:
        return None
    try:
        key = decrypt_value(cipher).strip()
    except Exception:  # noqa: BLE001 — 密文损坏或保险箱密钥换过，按没有密钥处理
        return None
    if not key:
        return None
    return base, key, model


def trial_unavailable_detail(user: User) -> str:
    settings = get_settings()
    base = (settings.router_base_url or settings.llm_base_url or "").strip()
    model = (settings.llm_model or "").strip()
    cipher = (getattr(user, "router_key_ciphertext", None) or "").strip()
    if cipher and (not base or not model):
        return TRIAL_NEEDS_GATEWAY
    return TRIAL_NEEDS_SSO
