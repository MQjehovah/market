"""能力认证 provider 注册表：按能力声明的 config_ui.provider 分发。"""

from __future__ import annotations

from collections.abc import Callable

_PROVIDERS: dict[str, Callable[[], object]] = {}


def register_auth_provider(name: str, factory: Callable[[], object]) -> None:
    _PROVIDERS[str(name).strip()] = factory


def provider_name(cap) -> str:
    ui = getattr(cap, "config_ui", None) or {}
    return str(ui.get("provider") or "").strip() if isinstance(ui, dict) else ""


def get_auth_provider(cap):
    name = provider_name(cap)
    factory = _PROVIDERS.get(name)
    return factory() if factory else None


def _dws_provider():
    from app.dws.provider import DwsAuthProvider

    return DwsAuthProvider()


register_auth_provider("dws", _dws_provider)
