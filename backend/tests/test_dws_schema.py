"""DWS 命令白名单与 argv 映射。"""

import pytest

from app.dws.schema import (
    DwsCommand,
    DwsCommandError,
    build_argv,
    normalize_command,
    parse_schema_json,
    validate_mode,
)


def _schema(effect="read"):
    return DwsCommand(
        cli_path="calendar event list",
        effect=effect,
        parameters={
            "start": {"type": "string"},
            "limit": {"type": "integer"},
            "all": {"type": "boolean"},
            "user-ids": {"type": "array", "format": "csv"},
        },
    )


def test_command_whitelist():
    assert normalize_command("calendar event list") == "calendar event list"
    with pytest.raises(DwsCommandError):
        normalize_command("auth logout")
    with pytest.raises(DwsCommandError):
        normalize_command("unknown thing")


def test_parse_schema():
    raw = """prefix
    {"cli_path":"calendar event list","effect":"read","confirmation":"not_required",
     "parameters":{"limit":{"type":"integer"}},"agent_summary":"查询日程"}
    """
    cmd = parse_schema_json(raw, "calendar event list")
    assert cmd.effect == "read"
    assert cmd.parameters["limit"]["type"] == "integer"
    assert cmd.description == "查询日程"


def test_build_argv_maps_types():
    argv = build_argv(
        "calendar event list",
        {"start": "2026-01-01", "limit": 20, "all": True, "user-ids": ["a", "b"]},
        ["x"],
        _schema(),
        bin_path="dws",
    )
    assert argv[:3] == ["dws", "calendar", "event"]
    assert "x" in argv
    assert "--start" in argv and "2026-01-01" in argv
    assert "--limit" in argv and "20" in argv
    assert "--all" in argv
    assert "--user-ids" in argv and "a,b" in argv
    assert argv[-2:] == ["--format", "json"]


def test_build_argv_adds_yes_for_write():
    argv = build_argv("calendar event create", {}, [], _schema("write"), bin_path="dws")
    assert argv[-3:] == ["--format", "json", "--yes"]


def test_build_argv_rejects_reserved_and_unknown():
    with pytest.raises(DwsCommandError):
        build_argv("calendar event list", {"token": "x"}, [], _schema(), bin_path="dws")
    with pytest.raises(DwsCommandError):
        build_argv("calendar event list", {"nope": "x"}, [], _schema(), bin_path="dws")


def test_mode_cannot_be_lower_than_effect():
    validate_mode("read", "read")
    validate_mode("write", "read")
    validate_mode("destructive", "write")
    with pytest.raises(DwsCommandError):
        validate_mode("read", "write")
