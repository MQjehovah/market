"""DWS 每用户工作空间：目录创建、权限与串行锁。

凭据始终由 DWS 自己写入 config/keychain，本模块只管理目录，不读取 token。
"""

from __future__ import annotations

import asyncio
import os
from contextlib import asynccontextmanager
from pathlib import Path

from app.config import get_settings

try:  # POSIX 跨进程锁；Windows 开发环境退化为进程内锁
    import fcntl
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]


class DwsWorkspace:
    """用户目录布局：<root>/<user_id>/dws/{config,keychain,files}。"""

    def __init__(self, root: Path | str | None = None) -> None:
        settings = get_settings()
        self.root = Path(root) if root is not None else settings.dws_workspace_path
        self.root = self.root.resolve()
        self.run_uid = int(settings.dws_run_uid or 0)
        self.run_gid = int(settings.dws_run_gid or 0)
        self._locks: dict[str, asyncio.Lock] = {}

    def user_dir(self, user_id: str) -> Path:
        safe = str(user_id or "").strip()
        if not safe or "/" in safe or "\\" in safe or safe in (".", ".."):
            raise ValueError("非法 DWS user_id")
        return self.root / safe / "dws"

    def config_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "config"

    def keychain_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "keychain"

    def files_dir(self, user_id: str) -> Path:
        return self.user_dir(user_id) / "files"

    def ensure(self, user_id: str) -> Path:
        base = self.user_dir(user_id)
        for path in (base, self.config_dir(user_id), self.keychain_dir(user_id), self.files_dir(user_id)):
            path.mkdir(parents=True, exist_ok=True)
            try:
                os.chmod(path, 0o700)
            except OSError:
                pass
            self._chown(path)
        return base

    def _chown(self, path: Path) -> None:
        if self.run_uid <= 0 or not hasattr(os, "geteuid") or os.geteuid() != 0:
            return
        try:
            os.chown(path, self.run_uid, self.run_gid or self.run_uid)
        except OSError:
            pass

    def env(self, user_id: str) -> dict[str, str]:
        base = self.ensure(user_id)
        return {
            "DWS_CONFIG_DIR": str(self.config_dir(user_id)),
            "DWS_KEYCHAIN_DIR": str(self.keychain_dir(user_id)),
            "HOME": str(base),
        }

    def _lock_for(self, user_id: str) -> asyncio.Lock:
        return self._locks.setdefault(user_id, asyncio.Lock())

    @asynccontextmanager
    async def lock(self, user_id: str):
        """同一用户的登录/执行串行，避免 DWS token 刷新竞争。

        进程内 asyncio.Lock + POSIX flock 两道；flock 文件放在用户 dws 目录。
        """
        async with self._lock_for(user_id):
            lock_path = self.user_dir(user_id) / ".lock"
            lock_path.parent.mkdir(parents=True, exist_ok=True)
            fh = None
            try:
                if fcntl is not None:
                    fh = open(lock_path, "a+", encoding="utf-8")  # noqa: SIM115
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
                yield
            finally:
                if fh is not None:
                    try:
                        fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
                    finally:
                        fh.close()
