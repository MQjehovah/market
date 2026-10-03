"""运行时可变配置：不改环境变量/重启即可生效（存 data/runtime_settings.json）。

读取方每次调用即读文件，因此改完立刻生效（热重载）。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.config import get_settings

_LOCK = threading.Lock()


def _path() -> Path:
    return Path(get_settings().data_dir) / "runtime_settings.json"


def load() -> dict[str, Any]:
    try:
        with open(_path(), encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _write(values: dict[str, Any]) -> dict[str, Any]:
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(values, f, ensure_ascii=False, indent=2)
    tmp.replace(path)
    return values


def save(values: dict[str, Any]) -> dict[str, Any]:
    """合并写入（值为 None 时删除该键= 恢复默认）。"""
    with _LOCK:
        cur = load()
        for key, val in (values or {}).items():
            if val is None:
                cur.pop(key, None)
            else:
                cur[key] = val
        return _write(cur)


def get_int(key: str, default: int) -> int:
    try:
        return int(load().get(key))
    except (TypeError, ValueError):
        return default


def agent_max_iterations() -> int:
    """Agent 工具调用最大轮数：运行时覆盖优先，其次环境/默认。"""
    return get_int("agent_max_iterations", get_settings().agent_max_iterations)
