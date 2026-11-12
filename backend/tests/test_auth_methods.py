"""认证方式：任一种完整即通过，不要求所有 env 同时存在。"""

from types import SimpleNamespace

from app.services.capability_secrets import auth_methods, select_method_env


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


def test_derive_password_method_from_env_keys():
    cap = SimpleNamespace(
        config_ui={},
        input_schema={
            "required_env": [
                "ERP_API_BASE_URL",
                "ERP_USERNAME",
                "ERP_PASSWORD",
                "DIGITAL_HUB_TASK_FILE",
            ],
            "env": {"ERP_USERNAME": "${ERP_USERNAME}"},
        },
    )
    methods = auth_methods(cap)
    assert [m["id"] for m in methods] == ["password"]
    assert methods[0]["label"] == "账号密码"
    fields = {f["env"]: f["label"] for f in methods[0]["fields"]}
    assert fields["ERP_USERNAME"] == "账号"
    assert fields["ERP_PASSWORD"] == "密码"
    assert fields["ERP_API_BASE_URL"] == "服务地址"
    assert fields["DIGITAL_HUB_TASK_FILE"] == "任务文件"


def test_derive_token_method_from_env_keys():
    cap = SimpleNamespace(
        config_ui={},
        input_schema={
            "required_env": ["DINGTALK_APP_KEY", "DINGTALK_APP_SECRET", "DINGTALK_AGENT_ID"],
            "env": {},
        },
    )
    methods = auth_methods(cap)
    assert [m["id"] for m in methods] == ["token"]
    assert methods[0]["label"] == "应用凭据"
    fields = {f["env"]: f["label"] for f in methods[0]["fields"]}
    assert fields["DINGTALK_APP_KEY"] == "App Key"
    assert fields["DINGTALK_APP_SECRET"] == "App Secret"
    assert fields["DINGTALK_AGENT_ID"] == "Agent ID"
