"""DWS device flow 解析与进程管理。"""

import asyncio

from app.dws.device_flow import DeviceLoginManager, parse_device_output
from app.dws.workspace import DwsWorkspace


def test_parse_device_output_chinese_and_url():
    line = (
        "授权码: ABCD-EFGH\n"
        "授权链接（已填入授权码）：https://login.dingtalk.com/oauth2/device/verify.htm?user_code=ABCD-EFGH"
    )
    code, url = parse_device_output(line)
    assert code == "ABCD-EFGH"
    assert "verify.htm" in url
    assert "user_code=ABCD-EFGH" in url


def test_parse_device_output_english_and_ansi():
    line = "\x1b[32muser_code: WXYZ-1234\x1b[0m https://login.dingtalk.com/device/verify?user_code=WXYZ-1234"
    code, url = parse_device_output(line)
    assert code == "WXYZ-1234"
    assert url.startswith("https://login.dingtalk.com/device/verify")


def test_parse_device_output_ignores_unrelated_line():
    assert parse_device_output("Step 2: waiting for authorization...") is None


class _FakeStderr:
    def __init__(self, lines):
        self._lines = [line.encode() for line in lines]

    async def readline(self):
        if not self._lines:
            return b""
        return self._lines.pop(0)


class _FakeProc:
    def __init__(self, lines, exit_code=0):
        self.stderr = _FakeStderr(lines)
        self.stdout = None
        self.returncode = None
        self._exit = asyncio.Event()
        self._code = exit_code

    async def wait(self):
        await self._exit.wait()
        self.returncode = self._code
        return self._code

    def terminate(self):
        self.returncode = -15
        self._exit.set()

    def kill(self):
        self._exit.set()


async def test_manager_start_and_authorize(tmp_path, monkeypatch):
    ws = DwsWorkspace(tmp_path)
    proc = _FakeProc(
        [
            "授权码: TEST-CODE\n",
            "授权链接（已填入授权码）：https://login.dingtalk.com/oauth2/device/verify.htm?user_code=TEST-CODE\n",
        ]
    )

    async def fake_exec(*args, **kwargs):
        assert args[0].endswith("dws") or args[0] == "dws"
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
    manager = DeviceLoginManager(ws, "dws")
    session = await manager.start("u-1")
    assert session.status == "authorizing"
    assert session.user_code == "TEST-CODE"
    assert "verify.htm" in session.verification_url

    proc._exit.set()
    await asyncio.wait_for(session.task, timeout=2)
    assert session.status == "authorized"
    assert (tmp_path / "u-1" / "dws" / "config").is_dir()


async def test_manager_unknown_session_returns_none(tmp_path):
    manager = DeviceLoginManager(DwsWorkspace(tmp_path), "dws")
    assert await manager.poll("u-1", "missing") is None
