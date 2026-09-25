"""能力 scope 目录雏形测试(P0; 暂不改变现有授权判定)。"""

import os
import sys
from types import SimpleNamespace

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from app.services.scopes import (  # noqa: E402
    SCOPE_CATALOG,
    capability_required_scopes,
    elevated_missing_scopes,
    missing_scopes,
    role_scopes,
    scope_catalog,
    scope_enforced,
)


def test_scope_catalog_shape():
    assert "capability:invoke" in SCOPE_CATALOG
    items = scope_catalog()
    assert all({"key", "description"} <= set(i) for i in items)


def test_capability_required_scopes_by_type_and_risk():
    assert capability_required_scopes(
        SimpleNamespace(type="mcp", risk_default="read")
    ) == {"capability:invoke", "mcp:read"}
    assert capability_required_scopes(
        SimpleNamespace(type="mcp", risk_default="destructive")
    ) == {"capability:invoke", "mcp:write"}
    assert capability_required_scopes(
        SimpleNamespace(type="tool", risk_default="read")
    ) == {"capability:invoke", "data:read"}


def test_role_scopes_and_missing():
    assert "capability:invoke" in role_scopes("admin")
    assert "capability:invoke" in role_scopes("service")
    assert "capability:invoke" in role_scopes("svc", runtime_roles={"svc"})
    # 普通 user 只有读能力; 执行类需要 step-up(P2 落地), 当前不强制
    assert "capability:invoke" not in role_scopes("user")

    cap = SimpleNamespace(type="mcp", risk_default="read")
    assert missing_scopes("user", cap)  # 非空: 提示需要 step-up
    assert missing_scopes("admin", cap) == set()


def test_scope_enforced_default_off(monkeypatch):
    monkeypatch.delenv("MARKET_SCOPE_ENFORCE", raising=False)
    assert scope_enforced() is False
    monkeypatch.setenv("MARKET_SCOPE_ENFORCE", "1")
    assert scope_enforced() is True


def test_elevated_missing_scopes_only_write_for_regular():
    read_cap = SimpleNamespace(type="mcp", risk_default="read")
    write_cap = SimpleNamespace(type="mcp", risk_default="destructive")
    # 已通过准入的普通用户: 只读不额外要求; 写/高危需 step-up
    assert elevated_missing_scopes("user", read_cap) == set()
    assert elevated_missing_scopes("user", write_cap) == {"mcp:write"}
    # admin / runtime 授予角色具备写 scope → 无缺失
    assert elevated_missing_scopes("admin", write_cap) == set()
    assert elevated_missing_scopes("svc", write_cap, runtime_roles={"svc"}) == set()
