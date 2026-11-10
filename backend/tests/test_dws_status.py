"""DWS 认证状态解析。"""

import json

from app.dws.status import parse_auth_status, status_to_account_fields


def test_parse_flat_auth_status_with_profiles():
    auth = json.dumps(
        {
            "authenticated": True,
            "expires_at": "2026-11-11T12:00:00+08:00",
            "refresh_expires_at": "2026-12-11T12:00:00+08:00",
            "corp_id": "ding1",
            "corp_name": "霞智科技",
            "user_id": "u1",
            "user_name": "季明清",
            "client_id": "dingclient",
        }
    )
    profiles = json.dumps(
        {
            "currentProfile": "ding1:u1",
            "profiles": [
                {
                    "corpId": "ding1",
                    "corpName": "霞智科技",
                    "userId": "u1",
                    "userName": "季明清",
                    "clientId": "dingclient",
                }
            ],
        }
    )
    status = parse_auth_status(auth, profiles)
    assert status.authenticated is True
    assert status.status == "authorized"
    assert status.current_profile == "ding1:u1"
    assert status.corp_name == "霞智科技"
    assert status.profiles[0]["isCurrent"] is True
    assert status.access_expires_at is not None
    assert status.access_expires_at.hour == 4  # +08:00 -> UTC


def test_parse_logged_out_is_unauthorized():
    status = parse_auth_status(json.dumps({"success": True, "authenticated": False, "message": "未登录"}))
    assert status.authenticated is False
    assert status.status == "unauthorized"
    assert status.error == "未登录"


def test_parse_expired_refresh_is_expired():
    status = parse_auth_status(
        json.dumps({"authenticated": False}),
        json.dumps(
            {
                "currentProfile": "ding1:u1",
                "profiles": [{"corpId": "ding1", "userId": "u1", "refreshExpAt": "2020-01-01T00:00:00+08:00"}],
            }
        ),
    )
    assert status.status == "expired"
    assert status.refresh_expires_at is not None


def test_parse_garbage_returns_error_not_raise():
    status = parse_auth_status("not json at all")
    assert status.authenticated is False
    assert status.status == "unauthorized"
    assert status.error

    fields = status_to_account_fields(status)
    assert fields["status"] == "unauthorized"
    assert "access_token" not in fields
