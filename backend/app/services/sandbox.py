"""工作流 code 节点沙箱：在受限子进程中执行用户代码（基础隔离）。

- 目前支持 Python（进程隔离 + 超时 + 临时工作目录；`-I` 隔离模式，忽略用户 site/环境）。
- 用户代码约定：定义 `def main(**inputs)` 返回结果，或设置变量 `result`。
- 说明：这是"基础隔离"，非 microVM/syscall 级；生产如需强隔离应下沉到容器/微虚机。
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import sys
import tempfile
from typing import Any

_RUNNER = r'''
import json
import sys

inputs = {}
try:
    with open(sys.argv[1], encoding="utf-8") as f:
        inputs = json.load(f)
except Exception:
    inputs = {}

g = {"inputs": inputs, "args": inputs}
with open(sys.argv[2], encoding="utf-8") as f:
    code = f.read()

exec(compile(code, "main.py", "exec"), g)

result = None
if callable(g.get("main")):
    try:
        result = g["main"](**inputs)
    except TypeError:
        result = g["main"](inputs)
else:
    result = g.get("result")

print("__WF_RESULT__" + json.dumps({"result": result}, ensure_ascii=False, default=str))
'''


async def run_code(
    language: str,
    code: str,
    inputs: dict[str, Any] | None = None,
    timeout: int = 30,
) -> dict[str, Any]:
    """执行用户代码，返回 {"result": ...}；超时/非零退出/无结果抛异常。"""
    if str(language or "python").lower() not in ("python", "python3", "py"):
        raise ValueError(f"暂不支持的语言: {language}（当前仅支持 python）")
    if not str(code or "").strip():
        raise ValueError("code 为空")

    workdir = tempfile.mkdtemp(prefix="wf-code-")
    try:
        inp_path = os.path.join(workdir, "inputs.json")
        code_path = os.path.join(workdir, "main.py")
        runner_path = os.path.join(workdir, "_runner.py")
        with open(inp_path, "w", encoding="utf-8") as f:
            json.dump(inputs or {}, f, ensure_ascii=False)
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(code)
        with open(runner_path, "w", encoding="utf-8") as f:
            f.write(_RUNNER)

        proc = await asyncio.create_subprocess_exec(
            sys.executable,
            "-I",
            runner_path,
            inp_path,
            code_path,
            cwd=workdir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            out, err = await asyncio.wait_for(proc.communicate(), timeout=max(1, int(timeout)))
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            raise TimeoutError(f"code 执行超时（>{timeout}s）")

        stdout = out.decode("utf-8", "replace")
        stderr = err.decode("utf-8", "replace")
        if proc.returncode != 0:
            raise RuntimeError(f"code 运行失败（exit {proc.returncode}）: {stderr[:600]}")
        marker = "__WF_RESULT__"
        line = next((ln for ln in stdout.splitlines() if ln.startswith(marker)), "")
        if not line:
            raise RuntimeError(f"code 未返回结果: {stdout[:300]}{stderr[:200]}")
        return json.loads(line[len(marker):])
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
