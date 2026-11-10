"""DWS 命令白名单、schema 缓存与 argv 组装。"""

from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass, field
from typing import Any

from app.dws.workspace import DwsWorkspace

ALLOWED_SERVICES = {
    "agoal",
    "aisearch",
    "aitable",
    "attendance",
    "calendar",
    "chat",
    "contact",
    "contract",
    "devapp",
    "devdoc",
    "ding",
    "doc",
    "drive",
    "hrbrain",
    "live",
    "mail",
    "minutes",
    "oa",
    "recruit",
    "report",
    "sheet",
    "todo",
    "whiteboard",
    "wiki",
}
READ_ONLY_COMMANDS = {"schema", "help"}
FORBIDDEN_SERVICES = {
    "auth",
    "profile",
    "config",
    "plugin",
    "upgrade",
    "event",
    "completion",
    "cache",
    "recovery",
    "mcp",
    "skill",
}
RESERVED_FLAGS = {
    "token",
    "client-id",
    "client-secret",
    "profile",
    "format",
    "yes",
    "debug",
    "mock",
}
RANK = {"read": 0, "write": 1, "destructive": 2}
_ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")

_CACHE: dict[str, tuple[float, "DwsCommand"]] = {}
_CACHE_TTL = 300.0


class DwsCommandError(ValueError):
    """命令不在白名单、schema 不可用或参数非法。"""


@dataclass
class DwsCommand:
    cli_path: str
    effect: str = "write"
    confirmation: str = "user_required"
    parameters: dict[str, Any] = field(default_factory=dict)
    description: str = ""
    examples: list[str] = field(default_factory=list)


def normalize_command(command: str) -> str:
    text = " ".join(str(command or "").split())
    if not text:
        raise DwsCommandError("缺少 DWS 命令")
    parts = text.split(" ")
    if parts[0] not in ALLOWED_SERVICES | READ_ONLY_COMMANDS:
        if parts[0] in FORBIDDEN_SERVICES:
            raise DwsCommandError(f"不允许调用 DWS 管理命令: {parts[0]}")
        raise DwsCommandError(f"未知/不允许的 DWS 服务: {parts[0]}")
    return text


def validate_mode(declared: str, effect: str) -> None:
    d = str(declared or "").strip().lower()
    e = str(effect or "write").strip().lower()
    if d not in RANK:
        raise DwsCommandError("mode 必须是 read / write / destructive")
    if e not in RANK:
        e = "write"
    if RANK[d] < RANK[e]:
        raise DwsCommandError(f"mode={d} 低于命令实际风险 {e}，拒绝执行")


def parse_schema_json(raw: str, command: str) -> DwsCommand:
    text = _ANSI.sub("", raw or "").strip()
    start = text.find("{")
    if start < 0:
        raise DwsCommandError("dws schema 未返回 JSON")
    data: dict[str, Any] | None = None
    for end in range(len(text), start, -1):
        try:
            value = json.loads(text[start:end])
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            data = value
            break
    if data is None:
        raise DwsCommandError("dws schema JSON 解析失败")
    effect = str(data.get("effect") or "write").strip().lower()
    if effect not in RANK:
        effect = "write"
    params = data.get("parameters")
    return DwsCommand(
        cli_path=str(data.get("cli_path") or command),
        effect=effect,
        confirmation=str(data.get("confirmation") or "user_required"),
        parameters=params if isinstance(params, dict) else {},
        description=str(data.get("agent_summary") or data.get("description") or ""),
        examples=[str(x) for x in (data.get("examples") or []) if str(x).strip()],
    )


async def load_command(
    workspace: DwsWorkspace,
    user_id: str,
    command: str,
    *,
    bin_path: str,
    extra_env: dict[str, str] | None = None,
    timeout: float = 30.0,
) -> DwsCommand:
    import asyncio
    import os

    cmd = normalize_command(command)
    first = cmd.split(" ", 1)[0]
    if first in READ_ONLY_COMMANDS:
        return DwsCommand(cli_path=cmd, effect="read", confirmation="not_required")
    cache_key = f"{bin_path}:{cmd}"
    cached = _CACHE.get(cache_key)
    if cached and cached[0] > time.monotonic():
        return cached[1]

    env = dict(os.environ)
    env.update(workspace.env(user_id))
    env.setdefault("DWS_LANG", "zh")
    env.setdefault("HOME", str(workspace.user_dir(user_id)))
    if extra_env:
        env.update(extra_env)
    try:
        proc = await asyncio.create_subprocess_exec(
            bin_path,
            "schema",
            cmd,
            "--compact",
            "--format",
            "json",
            cwd=str(workspace.ensure(user_id)),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError as exc:
        raise DwsCommandError("dws schema 查询超时") from exc
    except Exception as exc:  # noqa: BLE001
        raise DwsCommandError(f"dws schema 启动失败: {type(exc).__name__}: {exc}") from exc
    if proc.returncode != 0:
        err = (stderr or b"").decode("utf-8", errors="replace").strip()[:500]
        raise DwsCommandError(f"命令不可用: {err or proc.returncode}")
    command_schema = parse_schema_json((stdout or b"").decode("utf-8", errors="replace"), cmd)
    _CACHE[cache_key] = (time.monotonic() + _CACHE_TTL, command_schema)
    return command_schema


def clear_schema_cache() -> None:
    _CACHE.clear()


def build_argv(
    command: str,
    args: dict[str, Any] | None,
    positional: list[str] | None,
    schema: DwsCommand,
    *,
    bin_path: str,
) -> list[str]:
    cmd = normalize_command(command)
    parts = cmd.split(" ")
    argv = [bin_path, *parts]
    for value in positional or []:
        text = str(value)
        if text.startswith("-"):
            raise DwsCommandError("positional 不允许以 - 开头")
        argv.append(text)

    declared = schema.parameters or {}
    for raw_key, value in (args or {}).items():
        key = str(raw_key).strip().lstrip("-")
        if not key or key in RESERVED_FLAGS:
            raise DwsCommandError(f"不允许的参数: {raw_key}")
        if declared and key not in declared:
            raise DwsCommandError(f"命令不支持参数: {key}")
        if value is None:
            continue
        spec = declared.get(key) if isinstance(declared.get(key), dict) else {}
        flag = f"--{key}"
        if isinstance(value, bool):
            if value:
                argv.append(flag)
            continue
        if isinstance(value, (int, float)):
            argv.extend([flag, str(value)])
            continue
        if isinstance(value, (list, tuple)):
            items = [str(v) for v in value if v is not None]
            if not items:
                continue
            fmt = str(spec.get("format") or "").lower()
            if "csv" in fmt or "comma" in fmt:
                argv.extend([flag, ",".join(items)])
            else:
                for item in items:
                    argv.extend([flag, item])
            continue
        if isinstance(value, dict):
            argv.extend([flag, json.dumps(value, ensure_ascii=False, separators=(",", ":"))])
            continue
        argv.extend([flag, str(value)])

    argv.extend(["--format", "json"])
    if schema.effect != "read":
        argv.append("--yes")
    return argv
