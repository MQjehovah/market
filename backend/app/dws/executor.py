"""DWS 命令执行器：market 侧按用户 workspace 运行，不经 shell。"""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import get_settings
from app.dws.accounts import get_or_create_account
from app.dws.files import (
    DwsFileError,
    get_file,
    resolve_file,
    safe_name,
    save_output,
)
from app.dws.schema import (
    DwsCommandError,
    build_argv,
    load_command,
    normalize_command,
    validate_mode,
)
from app.dws.workspace import DwsWorkspace
from app.models import Capability, DwsCall, User

_ABS_PATH = re.compile(r"^(?:[A-Za-z]:[\\/]|/|\\|~[\\/])")
_PATH_TRAVERSAL = re.compile(r"(?:^|[\\/])\.\.(?:[\\/]|$)")


class DwsExecuteError(ValueError):
    pass


def _error(code: str, message: str, **extra: Any) -> dict[str, Any]:
    return {"success": False, "error": code, "message": message, **extra}


def _check_arg_paths(args: dict[str, Any], mapped: set[str]) -> None:
    for key, value in args.items():
        if key in mapped or not isinstance(value, str):
            continue
        text = value.strip()
        if not text:
            continue
        if _ABS_PATH.match(text) or _PATH_TRAVERSAL.search(text):
            raise DwsExecuteError(f"参数 {key} 不允许绝对路径/路径穿越；文件请走 inputs/outputs")


async def _page_url(db: AsyncSession) -> str:
    base = get_settings().public_base_url.rstrip("/")
    cap_id = await db.scalar(
        select(Capability.id)
        .where(Capability.name == "dingtalk-dws", Capability.status == "published")
        .order_by(Capability.created_at.desc())
    )
    return f"{base}/capabilities/{cap_id}?tab=config" if cap_id else f"{base}/dws"


def _preexec():
    settings = get_settings()
    uid, gid = int(settings.dws_run_uid or 0), int(settings.dws_run_gid or 0)
    if uid <= 0 or not hasattr(os, "geteuid") or os.geteuid() != 0:
        return None

    def drop():
        if gid > 0:
            os.setgid(gid)
        os.setuid(uid)

    return drop


def _json_from_output(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    start = min((i for i in (raw.find("{"), raw.find("[")) if i >= 0), default=-1)
    if start < 0:
        return raw
    for end in range(len(raw), start, -1):
        try:
            return json.loads(raw[start:end])
        except json.JSONDecodeError:
            continue
    return raw


async def execute_dws(
    db: AsyncSession,
    user: User,
    cap: Capability,
    params: dict[str, Any],
) -> dict[str, Any]:
    settings = get_settings()
    ws = DwsWorkspace()
    command = str((params or {}).get("command") or "").strip()
    mode = str((params or {}).get("mode") or "read").strip().lower()
    args = dict((params or {}).get("args") or {})
    positional = list((params or {}).get("positional") or [])
    confirm = bool((params or {}).get("confirm"))
    profile = str((params or {}).get("profile") or "").strip()
    inputs = list((params or {}).get("inputs") or [])
    outputs = list((params or {}).get("outputs") or [])

    account = await get_or_create_account(db, user.id)
    page_url = await _page_url(db)
    if account.status != "authorized":
        return _error(
            "dws_not_authorized",
            "钉钉 DWS 尚未授权；请打开能力配置页扫码登录",
            login_url=page_url,
            page_url=page_url,
            status=account.status,
        )
    if mode not in ("read", "write", "destructive"):
        return _error("dws_bad_mode", "mode 必须是 read / write / destructive")

    extra_env: dict[str, str] = {}
    if settings.dws_client_id and settings.dws_client_secret:
        extra_env["DWS_CLIENT_ID"] = settings.dws_client_id
        extra_env["DWS_CLIENT_SECRET"] = settings.dws_client_secret
    try:
        command = normalize_command(command)
        schema = await load_command(
            ws, user.id, command, bin_path=settings.dws_bin, extra_env=extra_env
        )
        validate_mode(mode, schema.effect)
    except DwsCommandError as exc:
        return _error("dws_bad_command", str(exc))

    if schema.effect != "read" and not confirm:
        return _error(
            "dws_confirmation_required",
            "写操作需要用户确认；请先征得用户同意，再传 confirm=true",
            effect=schema.effect,
        )

    call_id = uuid.uuid4().hex
    call_dir = ws.files_dir(user.id) / "calls" / call_id
    in_dir = call_dir / "in"
    out_dir = call_dir / "out"
    in_dir.mkdir(parents=True, exist_ok=True)
    out_dir.mkdir(parents=True, exist_ok=True)
    mapped: set[str] = set()
    try:
        for item in inputs:
            if not isinstance(item, dict):
                continue
            arg = str(item.get("arg") or "").strip()
            file_id = str(item.get("file_id") or "").strip()
            if not arg or not file_id:
                raise DwsExecuteError("inputs 必须包含 arg 和 file_id")
            row = await get_file(db, user, file_id)
            src = resolve_file(ws, user.id, row)
            dst = in_dir / safe_name(row.name)
            shutil.copyfile(src, dst)
            args[arg] = str(dst)
            mapped.add(arg)
        for item in outputs:
            if not isinstance(item, dict):
                continue
            arg = str(item.get("arg") or "").strip()
            name = safe_name(str(item.get("name") or "output.bin"))
            if not arg:
                raise DwsExecuteError("outputs 必须包含 arg")
            dst = out_dir / name
            args[arg] = str(dst)
            mapped.add(arg)
        _check_arg_paths(args, mapped)
    except (DwsFileError, DwsExecuteError) as exc:
        return _error("dws_bad_file", str(exc))

    try:
        argv = build_argv(command, args, positional, schema, bin_path=settings.dws_bin)
        if profile:
            if not re.fullmatch(r"[A-Za-z0-9_:.\-]{1,255}", profile):
                raise DwsExecuteError("profile 格式非法")
            argv.append("--profile")
            argv.append(profile)
    except (DwsCommandError, DwsExecuteError) as exc:
        return _error("dws_bad_command", str(exc))

    env = dict(os.environ)
    env.update(ws.env(user.id))
    env.update(extra_env)
    env.setdefault("DWS_LANG", "zh")
    env.setdefault("HOME", str(ws.user_dir(user.id)))
    env.setdefault("DWS_AGENT_PRODUCT", "rosiwit-agent")
    env.setdefault("DWS_AGENT_HOST", "cloud")

    t0 = time.monotonic()
    status = "ok"
    result: dict[str, Any]
    try:
        async with ws.lock(user.id):
            subprocess_kwargs: dict[str, Any] = {
                "cwd": str(call_dir),
                "env": env,
                "stdin": asyncio.subprocess.DEVNULL,
                "stdout": asyncio.subprocess.PIPE,
                "stderr": asyncio.subprocess.PIPE,
            }
            pre = _preexec()
            if pre is not None:
                subprocess_kwargs["preexec_fn"] = pre
            proc = await asyncio.create_subprocess_exec(*argv, **subprocess_kwargs)
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=max(1, settings.dws_call_timeout)
                )
            except asyncio.TimeoutError:
                proc.kill()
                with contextlib_suppress():
                    await proc.wait()
                status = "timeout"
                result = _error("dws_timeout", f"DWS 执行超过 {settings.dws_call_timeout} 秒")
            else:
                out_text = (stdout or b"").decode("utf-8", errors="replace")
                err_text = (stderr or b"").decode("utf-8", errors="replace")
                if proc.returncode == 0:
                    data = _json_from_output(out_text)
                    result = {
                        "success": True,
                        "command": command,
                        "effect": schema.effect,
                        "data": data,
                        "duration_ms": int((time.monotonic() - t0) * 1000),
                    }
                    if len(out_text.encode("utf-8")) > 200_000:
                        oversized = out_dir / "result.json"
                        oversized.write_text(out_text, encoding="utf-8")
                        result["truncated"] = True
                        result["data"] = None
                        result["output_name"] = "result.json"
                else:
                    status = "error"
                    result = _error(
                        "dws_failed",
                        (err_text or out_text or f"dws 退出码 {proc.returncode}").strip()[:2000],
                        exit_code=proc.returncode,
                        effect=schema.effect,
                    )

            # 收集输出文件（含 result.json）
            out_rows = []
            if out_dir.exists():
                for path in sorted(out_dir.iterdir()):
                    if not path.is_file():
                        continue
                    row = await save_output(db, user, path, workspace=ws)
                    out_rows.append({"file_id": row.id, "name": row.name, "size": row.size})
            if out_rows:
                result["output_files"] = out_rows
    except Exception as exc:  # noqa: BLE001
        status = "error"
        result = _error("dws_internal", f"{type(exc).__name__}: {exc}")

    audit = DwsCall(
        user_id=user.id,
        command=command[:255],
        mode=mode,
        effect=schema.effect,
        status=status,
        duration_ms=int((time.monotonic() - t0) * 1000),
        args_summary={
            "keys": sorted(str(k) for k in args.keys()),
            "positional": len(positional),
            "inputs": len(inputs),
            "outputs": len(outputs),
        },
        error=str(result.get("message") or result.get("error") or "")[:2000],
    )
    db.add(audit)
    account.last_used_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.flush()
    return result


class contextlib_suppress:
    """局部 suppress，避免在热路径导入整个 contextlib。"""

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return exc_type is not None
