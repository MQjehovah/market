"""工作流触发器：定时(cron) + webhook 事件。

- 定时：后台协程每 60s 扫描已发布的 workflow，命中 `trigger.cron` 则执行；`last_run` 落盘防重复。
- webhook：`POST /api/runtime/workflows/{name}/trigger`（`X-Workflow-Token` 校验）。
- 多 worker 部署下每个 worker 都会跑定时循环 → 需 `WORKERS=1` 或外部去重（见注释）。
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime

from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Capability, User
from app.services.workflows import execute_workflow, load_workflow_definition

logger = logging.getLogger("market.workflow_triggers")

_STATE: dict[str, str] = {}
_task: asyncio.Task | None = None


def _state_path() -> str:
    return os.path.join(str(get_settings().data_dir), "workflow_schedule.json")


def _load_state() -> None:
    global _STATE
    try:
        with open(_state_path(), encoding="utf-8") as f:
            data = json.load(f)
        _STATE = {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        _STATE = {}


def _save_state() -> None:
    try:
        with open(_state_path(), "w", encoding="utf-8") as f:
            json.dump(_STATE, f, ensure_ascii=False, indent=2)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"定时状态保存失败: {e}")


def _field_match(field: str, value: int) -> bool:
    if field.strip() == "*":
        return True
    for part in field.split(","):
        part = part.strip()
        if not part:
            continue
        if part.startswith("*/"):
            try:
                step = int(part[2:])
                if step > 0 and value % step == 0:
                    return True
            except ValueError:
                pass
            continue
        if "-" in part:
            try:
                lo, hi = (int(x) for x in part.split("-", 1))
                if lo <= value <= hi:
                    return True
            except ValueError:
                pass
            continue
        try:
            if int(part) == value:
                return True
        except ValueError:
            pass
    return False


def _cron_dow(now: datetime) -> int:
    # 标准 cron: 0/7=周日, 1=周一..6=周六；Python weekday: 周一=0..周日=6
    return (now.weekday() + 1) % 7


def cron_due(expr: str, now: datetime) -> bool:
    """5 字段 cron（分 时 日 月 周）是否命中当前分钟。"""
    fields = str(expr or "").split()
    if len(fields) != 5:
        return False
    return (
        _field_match(fields[0], now.minute)
        and _field_match(fields[1], now.hour)
        and _field_match(fields[2], now.day)
        and _field_match(fields[3], now.month)
        and _field_match(fields[4], _cron_dow(now))
    )


async def _run_due() -> int:
    now = datetime.now().replace(second=0, microsecond=0)
    stamp = now.strftime("%Y-%m-%d %H:%M")
    ran = 0
    async with SessionLocal() as db:
        caps = (
            await db.scalars(
                select(Capability).where(
                    Capability.type == "workflow", Capability.status == "published"
                )
            )
        ).all()
        for cap in caps:
            try:
                definition = load_workflow_definition(cap)
            except Exception:  # noqa: BLE001
                continue
            trig = definition.get("trigger") or {}
            if trig.get("type") != "schedule" or trig.get("enabled", True) is False:
                continue
            expr = str(trig.get("cron") or "")
            if not expr or not cron_due(expr, now):
                continue
            if _STATE.get(cap.name) == stamp:
                continue
            _STATE[cap.name] = stamp
            _save_state()
            author = await db.get(User, cap.author_id)
            if author is None:
                continue
            try:
                await execute_workflow(db, author, cap, trig.get("input") or {})
                ran += 1
                logger.info(f"[sched] 已运行工作流 {cap.name} @ {stamp}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[sched] 工作流 {cap.name} 运行失败: {e}")
    return ran


def start_scheduler() -> None:
    global _task
    if _task is not None:
        return
    _load_state()

    async def _loop() -> None:
        while True:
            try:
                await _run_due()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[sched] 调度循环异常: {e}")
            await asyncio.sleep(60)

    _task = asyncio.create_task(_loop())
    logger.info("[sched] 工作流定时触发器已启动（每 60s 扫描）")


def stop_scheduler() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        _task = None
