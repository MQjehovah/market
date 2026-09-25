"""出网 egress 白名单(防 SSRF)测试。"""

import os
import sys

import pytest
from fastapi import HTTPException

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.egress import (  # noqa: E402
    assert_egress_allowed,
    egress_enforced,
    is_private_ip,
)


def _resolver(*ips: str):
    def _r(host, port):
        return [(2, 1, 6, "", (ip, 0)) for ip in ips]

    return _r


def test_is_private_ip_ranges():
    for ip in ("127.0.0.1", "10.1.2.3", "192.168.1.1", "169.254.1.1", "100.64.0.1", "::1"):
        assert is_private_ip(ip) is True, ip
    for ip in ("8.8.8.8", "1.1.1.1", "2001:4860:4860::8888"):
        assert is_private_ip(ip) is False, ip
    assert is_private_ip("not-an-ip") is True  # 保守拒绝


def test_assert_egress_allowed_scheme_and_host():
    with pytest.raises(HTTPException) as e:
        assert_egress_allowed("ftp://example.com/x", allow_hosts=set())
    assert e.value.status_code == 400
    with pytest.raises(HTTPException) as e:
        assert_egress_allowed("https:///nohost", allow_hosts=set())
    assert e.value.status_code == 400


def test_assert_egress_allowlist_trusts_host(monkeypatch):
    monkeypatch.delenv("MCP_EGRESS_ALLOWLIST_ONLY", raising=False)
    # 白名单命中即放行(允许内网), 不做解析
    assert_egress_allowed(
        "http://internal.local:8090/mcp",
        allow_hosts={"internal.local"},
        resolver=_resolver("10.0.0.5"),
    )


def test_assert_egress_blocks_private_without_allowlist(monkeypatch):
    monkeypatch.delenv("MCP_EGRESS_ALLOWLIST_ONLY", raising=False)
    with pytest.raises(HTTPException) as e:
        assert_egress_allowed(
            "https://meta.example.com/latest",
            allow_hosts=set(),
            resolver=_resolver("169.254.169.254"),
        )
    assert e.value.status_code == 403


def test_assert_egress_allows_public(monkeypatch):
    monkeypatch.delenv("MCP_EGRESS_ALLOWLIST_ONLY", raising=False)
    assert_egress_allowed(
        "https://mcp.example.com/mcp",
        allow_hosts=set(),
        resolver=_resolver("8.8.8.8"),
    )


def test_assert_egress_allowlist_only_blocks_others(monkeypatch):
    monkeypatch.setenv("MCP_EGRESS_ALLOWLIST_ONLY", "1")
    with pytest.raises(HTTPException) as e:
        assert_egress_allowed(
            "https://evil.example.com/mcp",
            allow_hosts={"internal.local"},
            resolver=_resolver("8.8.8.8"),
        )
    assert e.value.status_code == 403
    # 白名单内正常
    assert_egress_allowed(
        "https://internal.local/mcp", allow_hosts={"internal.local"}, resolver=_resolver("10.0.0.5")
    )


def test_egress_enforced_gate(monkeypatch):
    monkeypatch.delenv("MCP_EGRESS_ENFORCE", raising=False)
    monkeypatch.delenv("MCP_EGRESS_ALLOW_HOSTS", raising=False)
    assert egress_enforced() is False
    # 仅配白名单不触发强制(需显式 ENFORCE)
    monkeypatch.setenv("MCP_EGRESS_ALLOW_HOSTS", "a.com,b.com")
    assert egress_enforced() is False
    monkeypatch.setenv("MCP_EGRESS_ENFORCE", "1")
    assert egress_enforced() is True
