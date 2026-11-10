"""认证方式：任一种完整即通过，不要求所有 env 同时存在。"""

from types import SimpleNamespace

from app.services.capability_secrets import select_method_env


def _cap():
    return SimpleNamespace(
        config_ui={
            "auth_methods": [
                {"id": "password", "fields": [{"env": "USER"}, {"env": "PASS"}]},
                {"id": "token", "fields": [{"env": "TOKEN"}]},
            ]
        }
    )


def test_any_complete_method_is_enough():
    env, missing = select_method_env(_cap(), {"USER": "u", "PASS": "p"})
    assert env == {"USER": "u", "PASS": "p"}
    assert missing == []

    env, missing = select_method_env(_cap(), {"TOKEN": "t"})
    assert env == {"TOKEN": "t"}
    assert missing == []


def test_incomplete_method_reports_missing_fields():
    env, missing = select_method_env(_cap(), {"USER": "u"})
    assert env == {}
    assert missing == ["PASS"]
