"""独立定时调度进程（指挥与执行分离）。

- 只做三件事：扫描已发布工作流的 schedule 触发器 → cron 到期 → **HTTP 派发给市场 Worker 执行**；
- 本进程**不执行**工作流：执行统一发生在市场 Web Worker（等待派发请求返回即执行结果）；
- 多实例部署时以锁文件**原子领取**（复用 workflow_triggers._claim），同一（工作流, 分钟）只派发一次。

运行：`python -m app.scheduler`（docker compose 的 market-scheduler 服务）。
启用独立调度时，市场 Web 侧应设置 `WORKFLOW_SCHEDULER_IN_APP=0`，避免 worker 内重复扫描。
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime
from urllib.parse import quote

import httpx
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.auth import create_access_token
from app.database import SessionLocal, init_db
from app.models import Capability, User
from app.services.workflow_triggers import (
    _STATE,
    _claim,
    _load_state,
    _save_state,
    cron_due,
)
from app.services.workflows import load_workflow_definition

logger = logging.getLogger("market.scheduler")

INTERNAL_URL = (os.environ.get("MARKET_INTERNAL_URL") or "http://market:8093").rstrip("/")
DISPATCH_TIMEOUT = float(os.environ.get("SCHEDULER_DISPATCH_TIMEOUT") or "3600")
SCAN_INTERVAL = float(os.environ.get("SCHEDULER_SCAN_INTERVAL") or "60")


async def _mint_token(db) -> str | None:
    """以首个可用管理员身份签发本地 JWT，用于向市场 Worker 派发。"""
    user = await db.scalar(
        select(User)
        .where(User.role == "admin", User.is_active.is_(True))
        .order_by(User.created_at)
        .limit(1)
    )
    if user is None:
        logger.warning("[sched] 无可用管理员账号，跳过派发")
        return None
    return create_access_token(user)


async def run_once() -> int:
    """扫描一轮 cron；到期则派发。返回本轮派发次数。"""
    now = datetime.now().replace(second=0, microsecond=0)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    dispatched = 0
    async with SessionLocal() as db:
        caps = (
            await db.scalars(
                select(Capability)
                .options(selectinload(Capability.artifacts))
                .where(
                    Capability.type == "workflow", Capability.status == "published"
                )
            )
        ).all()
        for cap in caps:
            try:
                definition = load_workflow_definition(cap)
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[sched] 工作流 {cap.name} 定义加载失败: {e}")
                continue
            trig = definition.get("trigger") or {}
            if trig.get("type") != "schedule" or trig.get("enabled", True) is False:
                continue
            expr = str(trig.get("cron") or "")
            if not expr or not cron_due(expr, now):
                continue
            if _STATE.get(cap.name) == stamp:
                continue
            if not _claim(cap.name, stamp):
                continue
            _STATE[cap.name] = stamp
            _save_state()
            token = await _mint_token(db)
            if not token:
                continue
            url = f"{INTERNAL_URL}/api/runtime/workflows/{quote(cap.name)}/executions"
            try:
                async with httpx.AsyncClient(timeout=DISPATCH_TIMEOUT) as client:
                    resp = await client.post(
                        url,
                        headers={"Authorization": f"Bearer {token}"},
                        json={"input": trig.get("input") or {}},
                    )
                state = "-"
                try:
                    state = str((resp.json() or {}).get("state") or "-")
                except Exception:  # noqa: BLE001
                    pass
                logger.info(
                    f"[sched] 派发 {cap.name} @ {stamp} -> HTTP {resp.status_code} state={state}"
                )
                dispatched += 1
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[sched] 派发失败 {cap.name}: {e}")
    return dispatched


async def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    await init_db()
    _load_state()
    logger.info(
        f"[sched] 独立调度进程启动：dispatch -> {INTERNAL_URL}（每 {int(SCAN_INTERVAL)}s 扫描）"
    )
    while True:
        try:
            await run_once()
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[sched] 扫描异常: {e}")
        await asyncio.sleep(SCAN_INTERVAL)


if __name__ == "__main__":
    asyncio.run(main())
