"""DWS 执行器：路由、风险与路径边界。"""

import asyncio

import pytest
from sqlalchemy import select

from app.database import SessionLocal
from app.dws import executor
from app.dws.schema import DwsCommand, DwsCommandError
from app.dws.workspace import DwsWorkspace
from app.models import Capability, DwsAccount, DwsCall, User


class _FakeProc:
    returncode = 0

    async def communicate(self):
        return b'{"ok": true}', b""


async def _setup(tmp_path, monkeypatch):
    async with SessionLocal() as db:
        cap = await db.scalar(select(Capability).where(Capability.name == "dingtalk-dws"))
        user = await db.scalar(select(User).where(User.username == "user"))
        db.add(DwsAccount(user_id=user.id, status="authorized"))
        await db.commit()
        return db, user, cap


async def test_execute_read_command_and_audit(client, tmp_path, monkeypatch):
    async with SessionLocal() as db:
        cap = await db.scalar(select(Capability).where(Capability.name == "dingtalk-dws"))
        user = await db.scalar(select(User).where(User.username == "user"))
        db.add(DwsAccount(user_id=user.id, status="authorized"))
        await db.commit()

        monkeypatch.setattr(executor, "DwsWorkspace", lambda: DwsWorkspace(tmp_path))

        async def fake_load(*args, **kwargs):
            return DwsCommand(cli_path="calendar event list", effect="read", parameters={})

        async def fake_exec(*args, **kwargs):
            return _FakeProc()

        monkeypatch.setattr(executor, "load_command", fake_load)
        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)

        result = await executor.execute_dws(
            db, user, cap, {"command": "calendar event list", "mode": "read"}
        )
        assert result["success"] is True
        assert result["data"] == {"ok": True}
        row = await db.scalar(select(DwsCall).where(DwsCall.user_id == user.id))
        assert row is not None
        assert row.command == "calendar event list"


async def test_execute_rejects_write_without_confirm(client, tmp_path, monkeypatch):
    async with SessionLocal() as db:
        cap = await db.scalar(select(Capability).where(Capability.name == "dingtalk-dws"))
        user = await db.scalar(select(User).where(User.username == "user"))
        db.add(DwsAccount(user_id=user.id, status="authorized"))
        await db.commit()
        monkeypatch.setattr(executor, "DwsWorkspace", lambda: DwsWorkspace(tmp_path))
        monkeypatch.setattr(
            executor,
            "load_command",
            lambda *a, **k: _async_value(DwsCommand(cli_path="calendar event create", effect="write")),
        )
        result = await executor.execute_dws(
            db, user, cap, {"command": "calendar event create", "mode": "write"}
        )
        assert result["success"] is False
        assert result["error"] == "dws_confirmation_required"


async def _async_value(value):
    return value


def test_path_guard_rejects_absolute_and_traversal():
    with pytest.raises(executor.DwsExecuteError):
        executor._check_arg_paths({"file": "/etc/passwd"}, set())
    with pytest.raises(executor.DwsExecuteError):
        executor._check_arg_paths({"file": "../secret"}, set())
    executor._check_arg_paths({"file": "/tmp/upload"}, {"file"})
