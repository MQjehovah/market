"""本地执行：调用本地 agent 引擎的单次任务入口（agent/scripts/run_task.py）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any


def default_agent_root() -> str:
    """默认取 workspace 下与 market 平级的 agent 仓库（可用 AGENT_ROOT 覆盖）。"""
    env = os.environ.get("AGENT_ROOT", "")
    if env:
        return env
    here = Path(__file__).resolve()  # market/consumer/cap/local_runner.py
    candidate = here.parents[3] / "agent"  # E:\ai\agent
    return str(candidate)


def find_agent_root(agent_root: str = "") -> Path:
    root = Path(agent_root or default_agent_root()).resolve()
    if not (root / "src").is_dir() or not (root / "scripts" / "run_task.py").is_file():
        raise FileNotFoundError(
            f"{root} 不是有效的 agent 仓库（缺少 src/ 或 scripts/run_task.py），"
            "请通过 --agent-root / AGENT_ROOT 指定"
        )
    return root


def run_local(
    agent_name: str,
    task: str,
    *,
    config_dir: str,
    agent_root: str = "",
    workspace: str = "",
    timeout: int = 900,
    python: str = "",
) -> dict[str, Any]:
    """在本地 agent 引擎中执行单次任务，返回 {ok, status, output, ...}。"""
    root = find_agent_root(agent_root)
    runner = root / "scripts" / "run_task.py"
    result_file = tempfile.NamedTemporaryFile(
        prefix="cap_result_", suffix=".json", delete=False
    ).name
    cmd = [
        python or sys.executable,
        str(runner),
        "--config-dir",
        config_dir,
        "--agent",
        agent_name,
        "--task",
        task,
        "--result-file",
        result_file,
    ]
    if workspace:
        cmd += ["--workspace", workspace]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        return {"ok": False, "status": "failed", "output": f"本地执行超时（>{timeout}s）"}

    try:
        payload = json.loads(Path(result_file).read_text(encoding="utf-8"))
    except (ValueError, OSError):
        payload = {}
    finally:
        try:
            os.unlink(result_file)
        except OSError:
            pass

    if payload:
        payload.setdefault("stderr_tail", (proc.stderr or "")[-800:])
        return payload
    return {
        "ok": False,
        "status": "failed",
        "output": "",
        "error": (proc.stderr or proc.stdout or "")[-2000:] or "本地执行失败（无结果）",
        "returncode": proc.returncode,
    }
