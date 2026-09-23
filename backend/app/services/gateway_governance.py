"""MCP 网关治理：进程内限流 + 熔断（单机 MVP；多实例再换 Redis）。"""

from __future__ import annotations

import time
from collections import defaultdict, deque
from threading import Lock
from typing import Deque

from app.config import get_settings

_lock = Lock()
_hits: dict[str, Deque[float]] = defaultdict(deque)
_failures: dict[str, int] = defaultdict(int)
_open_until: dict[str, float] = {}


def _now() -> float:
    return time.monotonic()


def check_rate_limit(key: str) -> tuple[bool, str]:
    """返回 (允许, 拒绝原因)。"""
    limit = max(1, get_settings().mcp_gateway_rate_limit_per_minute)
    window = 60.0
    now = _now()
    with _lock:
        q = _hits[key]
        while q and now - q[0] > window:
            q.popleft()
        if len(q) >= limit:
            return False, f"网关限流：每分钟最多 {limit} 次（key={key}）"
        q.append(now)
    return True, ""


def check_circuit(capability_key: str) -> tuple[bool, str]:
    """熔断打开时拒绝。"""
    now = _now()
    with _lock:
        until = _open_until.get(capability_key, 0.0)
        if until > now:
            remain = int(until - now) + 1
            return False, f"能力熔断中，约 {remain}s 后恢复（{capability_key}）"
        if until and until <= now:
            _open_until.pop(capability_key, None)
            _failures[capability_key] = 0
    return True, ""


def record_success(capability_key: str) -> None:
    with _lock:
        _failures[capability_key] = 0
        _open_until.pop(capability_key, None)


def record_failure(capability_key: str) -> None:
    settings = get_settings()
    threshold = max(1, settings.mcp_gateway_circuit_fail_threshold)
    cooldown = max(1, settings.mcp_gateway_circuit_cooldown_seconds)
    with _lock:
        _failures[capability_key] = _failures.get(capability_key, 0) + 1
        if _failures[capability_key] >= threshold:
            _open_until[capability_key] = _now() + cooldown


def reset_governance_for_tests() -> None:
    with _lock:
        _hits.clear()
        _failures.clear()
        _open_until.clear()
