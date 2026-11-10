"""DWS device flow 登录：启动 `dws auth login --device` 并解析授权码。"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.dws.workspace import DwsWorkspace

logger = logging.getLogger("market.dws.device")

_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
_CODE_RE = re.compile(r"(?:授权码|user[_ ]?code)\s*[:：=]\s*([A-Z0-9][A-Z0-9-]{4,})", re.I)
_URL_RE = re.compile(r"https://[^\s<>\"']*(?:verify|device)[^\s<>\"']*", re.I)
_URL_CODE_RE = re.compile(r"[?&]user_code=([A-Z0-9][A-Z0-9-]{4,})", re.I)
CODE_TTL_SECONDS = 900


@dataclass
class LoginSession:
    session_id: str
    user_id: str
    status: str = "authorizing"
    user_code: str = ""
    verification_url: str = ""
    expires_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(seconds=CODE_TTL_SECONDS)
    )
    error: str = ""
    started_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc).replace(tzinfo=None))
    process: Any = None
    task: Any = None
    stdout_task: Any = None
    _ready: asyncio.Event = field(default_factory=asyncio.Event)

    def to_public(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "status": self.status,
            "user_code": self.user_code,
            "verification_url": self.verification_url,
            "expires_at": self.expires_at.isoformat() + "Z" if self.expires_at else "",
            "error": self.error,
        }


def parse_device_output(line: str) -> tuple[str, str] | None:
    """从一行输出中提取 (user_code, verification_url)；没有则返回 None。"""
    text = _ANSI.sub("", line or "").strip()
    if not text:
        return None
    code = ""
    url = ""
    m = _CODE_RE.search(text)
    if m:
        code = m.group(1).strip()
    u = _URL_RE.search(text)
    if u:
        url = u.group(0).rstrip(".,;)）")
        cm = _URL_CODE_RE.search(url)
        if cm and not code:
            code = cm.group(1).strip()
    if not code and not url:
        return None
    return code, url


class DeviceLoginManager:
    """每个用户同一时刻只允许一个 device flow。"""

    def __init__(
        self,
        workspace: DwsWorkspace,
        bin_path: str,
        *,
        base_env: dict[str, str] | None = None,
    ) -> None:
        self.workspace = workspace
        self.bin_path = bin_path
        self.base_env = dict(base_env or {})
        self.sessions: dict[str, LoginSession] = {}
        self.by_user: dict[str, str] = {}

    async def start(self, user_id: str) -> LoginSession:
        await self.cancel(user_id)
        env = dict(os.environ)
        env.update(self.workspace.env(user_id))
        env.update(self.base_env)
        env.setdefault("DWS_LANG", "zh")
        env.setdefault("HOME", str(self.workspace.user_dir(user_id)))

        session = LoginSession(session_id=uuid.uuid4().hex, user_id=user_id)
        self.sessions[session.session_id] = session
        self.by_user[user_id] = session.session_id
        try:
            proc = await asyncio.create_subprocess_exec(
                self.bin_path,
                "auth",
                "login",
                "--device",
                "--no-browser",
                cwd=str(self.workspace.ensure(user_id)),
                env=env,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except Exception as exc:  # noqa: BLE001
            session.status = "error"
            session.error = f"无法启动 dws: {type(exc).__name__}: {exc}"
            logger.warning("DWS 登录进程启动失败 user=%s: %s", user_id, exc)
            return session
        session.process = proc
        session.task = asyncio.create_task(self._consume(session, proc), name=f"dws-login-{user_id}")
        session.stdout_task = asyncio.create_task(self._drain(proc.stdout), name=f"dws-login-out-{user_id}")
        # 给 CLI 一点时间打印授权码；超时也返回 authorizing，由前端轮询。
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(session._ready.wait(), timeout=3.0)
        return session

    async def _drain(self, stream: Any) -> None:
        if stream is None:
            return
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
        except Exception:  # noqa: BLE001
            pass

    async def _consume(self, session: LoginSession, proc: Any) -> None:
        tail: list[str] = []
        try:
            if proc.stderr is not None:
                while True:
                    raw = await proc.stderr.readline()
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace")
                    tail.append(line.strip())
                    if len(tail) > 20:
                        tail.pop(0)
                    parsed = parse_device_output(line)
                    if parsed:
                        code, url = parsed
                        if code and not session.user_code:
                            session.user_code = code
                        if url and not session.verification_url:
                            session.verification_url = url
                        session._ready.set()
            code = await proc.wait()
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            session.status = "error"
            session.error = f"{type(exc).__name__}: {exc}"
            session._ready.set()
            return
        if session.status == "authorizing":
            if code == 0:
                session.status = "authorized"
            elif code is None or code < 0:
                session.status = "cancelled"
            else:
                session.status = "error"
                session.error = " ".join(t for t in tail[-5:] if t)[:800] or f"dws 退出码 {code}"
        session._ready.set()

    async def poll(self, user_id: str, session_id: str) -> LoginSession | None:
        session = self.sessions.get(session_id)
        if session is None or session.user_id != user_id:
            return None
        if session.status == "authorizing" and session.expires_at <= datetime.now(timezone.utc).replace(tzinfo=None):
            await self.cancel(user_id)
            session.status = "error"
            session.error = "授权码已过期，请重新发起"
        return session

    async def cancel(self, user_id: str) -> None:
        sid = self.by_user.pop(user_id, "")
        session = self.sessions.get(sid) if sid else None
        if session is None:
            return
        proc = session.process
        if proc is not None and proc.returncode is None:
            with contextlib.suppress(ProcessLookupError):
                proc.terminate()
            try:
                await asyncio.wait_for(proc.wait(), timeout=3.0)
            except asyncio.TimeoutError:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
        for task in (session.task, session.stdout_task):
            if task is not None and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError, Exception):
                    await task
        if session.status == "authorizing":
            session.status = "cancelled"
        self.sessions.pop(sid, None)
