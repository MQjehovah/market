"""merge_edit_package_files / blank-core 单元测试。"""

from types import SimpleNamespace

from app.services.capabilities import _is_blank_core, draft_policy_kwargs, merge_edit_package_files


def test_is_blank_core_empty_and_json():
    core = frozenset({"PROMPT.md", "connection.json", "implementation/*.py"})
    assert _is_blank_core("PROMPT.md", b"  \n", core) is True
    assert _is_blank_core("PROMPT.md", b"hello", core) is False
    assert _is_blank_core("connection.json", b"{}", core) is True
    assert _is_blank_core("connection.json", b'{"transport":"stdio"}', core) is False
    assert _is_blank_core("implementation/server.py", b"", core) is True
    assert _is_blank_core("implementation/server.py", b"x=1\n", core) is False
    assert _is_blank_core("README.md", b"", core) is False


def test_draft_policy_kwargs():
    base = SimpleNamespace(
        access_policy="restricted",
        allowed_users=["alice"],
        install_policy="required",
        distribution="remote",
        risk_default="write",
        data_domain="设备",
    )
    assert draft_policy_kwargs(base) == {
        "access_policy": "restricted",
        "allowed_users": ["alice"],
        "install_policy": "required",
        "distribution": "remote",
        "risk_default": "write",
        "data_domain": "设备",
    }


def test_merge_edit_skips_blank_core_overlay(monkeypatch):
    published = SimpleNamespace(
        id="pub",
        version="1.0.0",
        artifacts=[SimpleNamespace(uri="p")],
    )
    draft = SimpleNamespace(
        id="draft",
        version="1.0.1",
        artifacts=[SimpleNamespace(uri="d")],
    )

    def fake_read(cap):
        if cap is None:
            return {}
        if cap.id == "pub":
            return {
                "PROMPT.md": b"from-published",
                "extra.txt": b"keep",
            }
        return {
            "PROMPT.md": b"   ",
            "extra.txt": b"draft-extra",
        }

    monkeypatch.setattr(
        "app.services.capabilities.read_capability_files", fake_read
    )
    monkeypatch.setattr(
        "app.services.capabilities.latest_published_with_package",
        lambda rows: published if rows else None,
    )
    files = merge_edit_package_files(
        draft, [published], core_text_files=frozenset({"PROMPT.md"})
    )
    assert files["PROMPT.md"] == b"from-published"
    assert files["extra.txt"] == b"draft-extra"
