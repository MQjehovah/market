"""统一出网 egress 白名单（防 SSRF）。

用于所有"由服务端发起的出网请求"（MCP 上游连接、registry 导入、fetch 等）：
- 仅允许 http/https；
- 拒绝解析到私有 / 回环 / 链路本地 / 保留 / 组播 / CGNAT(100.64.0.0/10) 的地址；
- 白名单 `MCP_EGRESS_ALLOW_HOSTS`（逗号分隔）内的主机视为可信（可内网），其余按公网校验；
- **灰度**：默认不强制（保持既有行为）；设置 `MCP_EGRESS_ENFORCE=1` 或配置了
  `MCP_EGRESS_ALLOW_HOSTS` 时启用强制。

校验"解析后的 IP"以缓解 DNS 重绑定（TOCTOU 仍有窗口，配合出网代理更佳）。
"""

from __future__ import annotations

import ipaddress
import os
import socket
from collections.abc import Callable, Iterable
from urllib.parse import urlparse

from fastapi import HTTPException, status

_TRUE = frozenset({"1", "true", "yes", "on"})
_CGNAT = ipaddress.ip_network("100.64.0.0/10")


def egress_allow_hosts() -> set[str]:
    raw = os.getenv("MCP_EGRESS_ALLOW_HOSTS", "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def egress_enforced() -> bool:
    if os.getenv("MCP_EGRESS_ENFORCE", "").strip().lower() in _TRUE:
        return True
    return bool(egress_allow_hosts())


def is_private_ip(ip: str) -> bool:
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # 无法解析为 IP → 保守拒绝
    if addr.version == 4 and addr in _CGNAT:
        return True
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
    )


def assert_egress_allowed(
    url: str,
    *,
    allow_hosts: set[str] | None = None,
    resolver: Callable[[str, object], Iterable[tuple]] | None = None,
) -> None:
    """校验出网 URL；不合规抛 HTTPException(400/403)。白名单主机视为可信。"""
    parsed = urlparse(url or "")
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"不允许的出网协议: {parsed.scheme or '(空)'}"
        )
    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "出网 URL 缺少主机名")

    allow = egress_allow_hosts() if allow_hosts is None else allow_hosts
    if allow:
        if host not in allow:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"目标主机不在出网白名单: {host}"
            )
        return

    resolve = resolver or socket.getaddrinfo
    try:
        infos = list(resolve(host, None))
    except OSError as exc:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, f"无法解析出网主机: {host}"
        ) from exc
    seen: set[str] = set()
    for info in infos:
        sockaddr = info[4]
        ip = str(sockaddr[0])
        if ip in seen:
            continue
        seen.add(ip)
        if is_private_ip(ip):
            raise HTTPException(
                status.HTTP_403_FORBIDDEN, f"目标解析到私有/保留地址, 拒绝出网: {host} -> {ip}"
            )


def enforce_egress(url: str, *, resolver=None) -> None:
    """按灰度开关强制校验：未启用则原样放行。"""
    if not url:
        return
    if not egress_enforced():
        return
    assert_egress_allowed(url, resolver=resolver)
