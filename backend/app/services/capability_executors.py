"""能力执行器注册表：按能力声明的 executor 分发，通用代码不写能力名。"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Capability, User

Executor = Callable[[AsyncSession, User, Capability, dict[str, Any]], Awaitable[dict[str, Any]]]

_EXECUTORS: dict[str, Executor] = {}


def register_executor(name: str, fn: Executor) -> None:
    _EXECUTORS[str(name).strip()] = fn


def get_executor(name: str) -> Executor | None:
    return _EXECUTORS.get(str(name or "").strip())


async def _dws_cli(db: AsyncSession, user: User, cap: Capability, params: dict[str, Any]) -> dict[str, Any]:
    # 延迟导入，保持通用模块不依赖具体 provider 的加载顺序
    from app.dws.executor import execute_dws

    return await execute_dws(db, user, cap, params)


register_executor("dws_cli", _dws_cli)
