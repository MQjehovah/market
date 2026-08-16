"""沙箱执行入口：安全解包 → AST 审计 → 子进程隔离执行。"""

import ast
import asyncio
import io
import json
import os
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from fastapi import HTTPException, status

from app.config import get_settings
from app.storage import get_storage

RUNNER_PATH = Path(__file__).resolve().parent / "runner.py"

# 允许导入的标准库（严格白名单；网络/进程/系统类一律禁止）
ALLOWED_MODULES = {
    "math", "json", "csv", "re", "datetime", "time", "random", "string",
    "collections", "itertools", "functools", "statistics", "typing", "uuid",
    "hashlib", "base64", "decimal", "fractions", "textwrap", "unicodedata",
    "bisect", "heapq", "operator", "dataclasses", "enum", "copy", "sys",
    "os", "pathlib", "io", "struct", "zlib", "contextlib", "abc", "types",
    "calendar", "zoneinfo", "difflib", "fnmatch", "glob", "gzip", "argparse",
    "warnings", "traceback",
}

BANNED_ATTRS = {
    ("os", "system"), ("os", "popen"), ("os", "startfile"),
    ("os", "remove"), ("os", "unlink"), ("os", "rmdir"), ("os", "removedirs"),
    ("os", "makedirs"), ("os", "chmod"), ("os", "chown"), ("os", "environ"),
    ("os", "kill"), ("os", "spawn*"), ("os", "exec*"),
}

BANNED_CALLS = {"eval", "exec", "compile", "__import__", "globals", "locals", "vars", "breakpoint"}


class SandboxError(Exception):
    pass


def audit_source(source: str) -> list[str]:
    """AST 审计：返回违规项列表，为空则通过。"""
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [f"语法错误：{exc}"]

    issues: list[str] = []

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                root = alias.name.split(".")[0]
                if root not in ALLOWED_MODULES:
                    issues.append(f"禁止导入模块：{alias.name}")
        elif isinstance(node, ast.ImportFrom) and node.module:
            root = node.module.split(".")[0]
            if root != "__future__" and root not in ALLOWED_MODULES:
                issues.append(f"禁止导入模块：{node.module}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in BANNED_CALLS:
                issues.append(f"禁止调用：{node.func.id}()")
        elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            for mod, attr in BANNED_ATTRS:
                if node.value.id == mod and (attr.endswith("*") or node.attr == attr):
                    issues.append(f"禁止访问：{mod}.{node.attr}")

    return list(dict.fromkeys(issues))


def _safe_extract(content: bytes, target: Path) -> None:
    """安全解压 zip：防止路径穿越，并限制解压总量（防 zip 炸弹）。"""
    settings = get_settings()
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise SandboxError("能力包不是有效的 zip 文件")
    total_size = 0
    for member in zf.infolist():
        if member.is_dir():
            continue
        total_size += member.file_size
        if total_size > settings.tool_max_extract_bytes:
            raise SandboxError(f"解压后总量超过限制（{settings.tool_max_extract_bytes // 1024 // 1024}MB），疑似 zip 炸弹")
        if member.file_size > settings.tool_max_extract_bytes:
            raise SandboxError(f"单文件超过解压限制：{member.filename}")
        resolved = (target / member.filename).resolve()
        if not str(resolved).startswith(str(target.resolve())):
            raise SandboxError(f"能力包包含非法路径：{member.filename}")
    zf.extractall(target)


def _kill_tree(proc) -> None:
    """结束子进程及其整个进程树（Windows 用 taskkill /T）。"""
    try:
        if os.name == "nt":
            subprocess.run(
                ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                capture_output=True,
                timeout=10,
            )
        else:
            proc.kill()
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


async def _run_subprocess(module_dir: Path, params: dict[str, Any], timeout: int) -> dict[str, Any]:
    settings = get_settings()
    env = {
        "PATH": os.environ.get("PATH", ""),
        "SystemRoot": os.environ.get("SystemRoot", os.environ.get("WINDIR", "")),
        "TEMP": str(module_dir),
        "PYTHONIOENCODING": "utf-8",
    }
    payload = json.dumps(params, ensure_ascii=False).encode("utf-8")
    proc = await asyncio.create_subprocess_exec(
        sys.executable,
        "-I",
        str(RUNNER_PATH),
        str(module_dir),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=str(module_dir),
        env=env,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(payload), timeout=timeout)
    except asyncio.TimeoutError:
        _kill_tree(proc)
        await proc.wait()
        raise SandboxError(f"工具执行超时（>{timeout}s）")

    out = out[: settings.tool_max_output_bytes]
    err = err[: settings.tool_max_output_bytes]
    if proc.returncode != 0:
        detail = err.decode("utf-8", errors="replace").strip() or "工具进程异常退出"
        raise SandboxError(detail[-2000:])
    try:
        parsed = json.loads(out.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        raise SandboxError(f"工具未返回合法 JSON 结果。stderr：{err.decode('utf-8', errors='replace')[-500:]}")
    if not parsed.get("ok"):
        raise SandboxError(str(parsed.get("error", "未知执行错误"))[-2000:])
    return parsed.get("result", {})


async def execute_tool_package(uri: str, params: dict[str, Any]) -> dict[str, Any]:
    """解包、审计并在隔离子进程中真实执行工具。"""
    settings = get_settings()
    try:
        content = get_storage().open(uri).read()
    except HTTPException as exc:
        raise SandboxError(f"读取能力包失败：{exc.detail}")
    if len(content) > settings.max_artifact_size_mb * 1024 * 1024:
        raise SandboxError("能力包超过大小限制")

    with tempfile.TemporaryDirectory(prefix="mk-sandbox-") as td:
        module_dir = Path(td)
        _safe_extract(content, module_dir)
        tool_file = module_dir / "implementation" / "tool.py"
        if not tool_file.is_file():
            raise SandboxError("工具包缺少 implementation/tool.py")

        source = tool_file.read_text(encoding="utf-8", errors="replace")
        issues = audit_source(source)
        if issues:
            raise SandboxError("安全审计未通过：" + "；".join(issues))

        result = await _run_subprocess(module_dir, params, settings.tool_timeout_seconds)
    return result


async def execute_tool(cap, params: dict[str, Any]) -> dict[str, Any]:
    """对外入口：有实现包则真实执行；声明 sandbox=false 的本地运行工具直接拒绝云端执行。"""
    artifacts = cap.__dict__.get("artifacts") if "artifacts" in cap.__dict__ else None
    if artifacts:
        try:
            content = get_storage().open(artifacts[-1].uri).read()
        except HTTPException:
            content = b""
        if content:
            try:
                with zipfile.ZipFile(io.BytesIO(content)) as zf:
                    sec = json.loads(zf.read("security.json").decode("utf-8"))
            except (zipfile.BadZipFile, KeyError, ValueError, UnicodeDecodeError):
                sec = {}
            if sec.get("sandbox") is False:
                return {
                    "execution": "local_only",
                    "ok": False,
                    "error": (
                        "该工具声明为本地运行工具（security.json sandbox=false），"
                        "不支持云端沙箱执行；请通过本地 agent 调用，或发布沙箱安全的实现包"
                    ),
                    "output": None,
                }
        try:
            result = await execute_tool_package(artifacts[-1].uri, params)
            return {"execution": "real", "ok": True, "output": result}
        except SandboxError as exc:
            return {
                "execution": "real",
                "ok": False,
                "error": str(exc),
                "output": None,
            }
    return {
        "execution": "simulated",
        "ok": True,
        "output": f"[模拟输出] 工具 {cap.name} 未上传实现包，返回占位结果。",
    }
