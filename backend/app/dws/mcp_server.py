"""钉钉 DWS MCP server：把 DWS CLI 暴露成标准 MCP 工具。

由 market 以 stdio 方式拉起；运行环境由 market 注入：
  DWS_CONFIG_DIR / DWS_KEYCHAIN_DIR / DWS_WORKDIR
本进程不接触数据库，也不保存 token。
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path
from typing import Any

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from app.dws.schema import (
    ALLOWED_SERVICES,
    DwsCommandError,
    build_argv,
    load_command_env,
    read_schema_env,
    validate_mode,
)

mcp = MCPServer("dingtalk-dws")


def _env() -> tuple[dict[str, str], Path]:
    env = dict(os.environ)
    config = Path(env.get("DWS_CONFIG_DIR") or (Path.home() / ".dws"))
    keychain = Path(env.get("DWS_KEYCHAIN_DIR") or (Path.home() / ".local/share/dws-cli"))
    work = Path(env.get("DWS_WORKDIR") or config.parent)
    for path in (config, keychain, work):
        path.mkdir(parents=True, exist_ok=True)
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    env["DWS_CONFIG_DIR"] = str(config)
    env["DWS_KEYCHAIN_DIR"] = str(keychain)
    env.setdefault("HOME", str(work))
    env.setdefault("DWS_LANG", "zh")
    env.setdefault("DWS_AGENT_PRODUCT", "rosiwit-agent")
    env.setdefault("DWS_AGENT_HOST", "cloud")
    return env, work


def _bin() -> str:
    return os.environ.get("DWS_BIN", "dws")


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


async def _run(
    command: str,
    args: dict[str, Any] | None,
    positional: list[str] | None,
    *,
    mode: str,
    profile: str = "",
    timeout: int = 180,
) -> dict[str, Any]:
    env, cwd = _env()
    try:
        schema = await load_command_env(
            command, bin_path=_bin(), env=env, cwd=str(cwd), timeout=30
        )
        validate_mode(mode, schema.effect)
        if schema.effect != "read" and str(mode).lower() == "read":
            raise DwsCommandError("该命令不是只读操作")
        argv = build_argv(command, args, positional, schema, bin_path=_bin())
        if profile:
            argv.extend(["--profile", profile])
        proc = await asyncio.create_subprocess_exec(
            *argv,
            cwd=str(cwd),
            env=env,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"success": False, "error": "dws_timeout", "message": f"DWS 执行超过 {timeout} 秒"}
    except DwsCommandError as exc:
        return {"success": False, "error": "dws_bad_command", "message": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"success": False, "error": "dws_internal", "message": f"{type(exc).__name__}: {exc}"}

    out = (stdout or b"").decode("utf-8", errors="replace")
    err = (stderr or b"").decode("utf-8", errors="replace")
    if proc.returncode != 0:
        return {
            "success": False,
            "error": "dws_failed",
            "exit_code": proc.returncode,
            "message": (err or out or "").strip()[:4000],
        }
    return {
        "success": True,
        "command": command,
        "effect": schema.effect,
        "data": _json_from_output(out),
    }


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=False))
async def dws_schema(path: str = "", query: str = "") -> dict:
    """查询钉钉 DWS 命令与参数。

    - path: 命令路径，如 calendar event list；留空返回服务清单
    - query: 按服务名/关键词过滤
    """
    env, cwd = _env()
    try:
        if path:
            data = await read_schema_env(path, bin_path=_bin(), env=env, cwd=str(cwd))
        else:
            data = {"services": sorted(ALLOWED_SERVICES)}
        if query and isinstance(data, dict):
            q = query.strip().lower()
            services = [s for s in data.get("services", []) if q in s.lower()]
            if services:
                data = {"services": services}
        return {"success": True, "path": path, "data": data}
    except DwsCommandError as exc:
        return {"success": False, "error": "dws_schema_failed", "message": str(exc)}


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=True, destructiveHint=False, openWorldHint=True))
async def dws_read(
    command: str,
    args: dict | None = None,
    positional: list | None = None,
) -> dict:
    """执行只读 DWS 命令，如 calendar event list / chat message list / doc search。

    command 为子命令路径；args 为具名参数（按 dws_schema 返回的 parameters）。
    """
    return await _run(command, args, positional, mode="read")


@mcp.tool(annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True))
async def dws_write(
    command: str,
    args: dict | None = None,
    positional: list | None = None,
    mode: str = "write",
    confirm: bool = False,
    profile: str = "",
) -> dict:
    """执行 DWS 写操作。必须先向用户说明影响并得到同意，再传 confirm=true。

    mode: write（普通写）或 destructive（删除/审批/权限变更等）。
    """
    if not confirm:
        return {
            "success": False,
            "error": "dws_confirmation_required",
            "message": "写操作需要用户确认；征得同意后再传 confirm=true",
        }
    if mode not in ("write", "destructive"):
        return {"success": False, "error": "dws_bad_mode", "message": "mode 必须是 write 或 destructive"}
    return await _run(command, args, positional, mode=mode, profile=profile)


def main() -> None:
    if os.environ.get("DWS_DEBUG"):
        print("dingtalk-dws MCP server starting", file=sys.stderr)
    mcp.run()


if __name__ == "__main__":
    main()
