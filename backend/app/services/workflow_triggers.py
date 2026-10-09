"""工作流触发器：定时(cron) + webhook 事件。

- 定时：后台协程每 60s 扫描已发布的 workflow，命中 `trigger.cron` 则执行；`last_run` 落盘防重复。
- webhook：`POST /api/runtime/workflows/{name}/trigger`（`X-Workflow-Token` 校验）。
- 多 worker 部署下每个 worker 都会跑定时循环 → 需 `WORKERS=1` 或外部去重（见注释）。
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
import os
from datetime import datetime

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select

from app.config import get_settings
from app.database import SessionLocal
from app.models import Capability, User
from app.services.workflows import execute_workflow, load_workflow_definition

logger = logging.getLogger("market.workflow_triggers")

_STATE: dict[str, str] = {}
_task: asyncio.Task | None = None
_stop: asyncio.Event | None = None


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


async def _gitlab_diff(db, body: dict, mr: dict | None = None) -> str:
    """经 `gitlab` 能力平台密钥拉取 MR changes / commit compare 的 diff 文本。"""
    try:
        from app.services.capability_secrets import resolve_capability_env

        env = await resolve_capability_env(db, "gitlab")
    except Exception:  # noqa: BLE001
        env = {}
    base = (env.get("GITLAB_URL") or os.environ.get("GITLAB_URL") or "").rstrip("/")
    token = (
        env.get("GITLAB_TOKEN")
        or env.get("GITLAB_PASSWORD")
        or os.environ.get("GITLAB_TOKEN")
        or ""
    )
    pid = (body.get("project") or {}).get("id")
    if not base or not token or not pid:
        return ""
    headers = {"PRIVATE-TOKEN": token}
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            if mr is not None and mr.get("iid"):
                url = f"{base}/api/v4/projects/{pid}/merge_requests/{mr['iid']}/changes"
                resp = await client.get(url, headers=headers)
                if resp.status_code >= 400:
                    return ""
                changes = (resp.json() or {}).get("changes") or []
                return "\n".join(
                    f"--- {c.get('old_path')} -> {c.get('new_path')}\n{c.get('diff', '')}"
                    for c in changes
                )[:200000]
            before, after = body.get("before"), body.get("after")
            if before and after and set(str(before)) != {"0"}:
                url = (
                    f"{base}/api/v4/projects/{pid}/repository/compare"
                    f"?from={before}&to={after}"
                )
                resp = await client.get(url, headers=headers)
                if resp.status_code >= 400:
                    return ""
                diffs = (resp.json() or {}).get("diffs") or []
                return "\n".join(
                    f"--- {c.get('old_path')} -> {c.get('new_path')}\n{c.get('diff', '')}"
                    for c in diffs
                )[:200000]
    except Exception as e:  # noqa: BLE001
        logger.warning(f"[gitlab] 拉取 diff 失败: {e}")
    return ""


async def gitlab_trigger_input(db, trigger: dict, headers: dict, body: dict) -> dict:
    """校验 GitLab webhook（X-Gitlab-Token）并把 payload 映射为工作流入参。"""
    token = str((trigger or {}).get("token") or "")
    provided = str((headers or {}).get("x-gitlab-token") or "")
    if not token or not hmac.compare_digest(token, provided):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的 X-Gitlab-Token")
    body = body or {}
    kind = str(body.get("object_kind") or "").lower()
    proj = body.get("project") or {}
    inp: dict = {
        "project": proj.get("path_with_namespace") or proj.get("name") or "",
        "project_id": proj.get("id"),
        "event": kind,
        "event_header": str((headers or {}).get("x-gitlab-event") or ""),
        "author": body.get("user_name") or (body.get("user") or {}).get("name") or "",
        "diff": "",
    }
    if "merge_request" in kind:
        oa = body.get("object_attributes") or {}
        inp.update(
            {
                "mr_iid": str(oa.get("iid") or ""),
                "title": oa.get("title") or "",
                "description": oa.get("description") or "",
                "source_branch": oa.get("source_branch") or "",
                "target_branch": oa.get("target_branch") or "",
                "action": oa.get("action") or "",
                "web_url": oa.get("url") or "",
            }
        )
        inp["diff"] = await _gitlab_diff(db, body, mr=oa)
    elif "push" in kind:
        commits = body.get("commits") or []
        inp.update(
            {
                "ref": body.get("ref") or "",
                "commit": (commits[-1].get("id") if commits else body.get("after") or ""),
                "commit_message": (commits[-1].get("message") if commits else ""),
                "commits": [c.get("id") for c in commits][:50],
            }
        )
        inp["diff"] = await _gitlab_diff(db, body)
    return inp


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
    global _task, _stop
    if _task is not None:
        return
    _load_state()
    stop = asyncio.Event()
    _stop = stop

    async def _loop() -> None:
        while True:
            try:
                await _run_due()
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[sched] 调度循环异常: {e}")
            try:
                # 可中断等待：收到停止信号立即退出，避免固定 sleep(60) 拖慢关闭
                await asyncio.wait_for(stop.wait(), timeout=60)
                return
            except asyncio.TimeoutError:
                continue

    _task = asyncio.create_task(_loop())
    logger.info("[sched] 工作流定时触发器已启动（每 60s 扫描）")


async def stop_scheduler(timeout: float = 5.0) -> None:
    """优雅停止调度循环：先让当前一轮扫描跑完再退出（不打断进行中的 DB 操作）。

    直接 cancel 正在执行 DB 查询的任务会让 SQLAlchemy greenlet 清理与 aiosqlite
    连接生命周期相撞，留下无法完成的 Future 卡死事件循环关闭（测试 teardown 曾因
    此永久挂起）；仅在超时后兜底 cancel。
    """
    global _task, _stop
    task, _task = _task, None
    stop, _stop = _stop, None
    if task is None:
        return
    if stop is not None:
        stop.set()
    try:
        await asyncio.wait_for(asyncio.shield(task), timeout)
    except asyncio.TimeoutError:
        logger.warning("[sched] 停止超时，强制取消定时任务")
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
