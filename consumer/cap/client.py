"""market 能力层 HTTP 客户端：登录、目录同步、下载、云端 runtime、A2A JSON-RPC。"""

from __future__ import annotations

import os
import uuid
from typing import Any
from urllib.parse import quote

import httpx


class CapabilityLayerError(RuntimeError):
    """能力层调用失败。"""


def default_base_url() -> str:
    return os.environ.get("CAP_URL", "http://127.0.0.1:8093").rstrip("/")


class MarketClient:
    """能力层 REST + A2A 客户端（独立于市场后端代码，仅依赖 httpx）。"""

    def __init__(self, base_url: str = "", token: str = ""):
        self.base_url = (base_url or default_base_url()).rstrip("/")
        self.token = token or os.environ.get("CAP_TOKEN", "")

    # ---------- 基础 ----------

    def _headers(self, json_body: bool = False) -> dict[str, str]:
        headers: dict[str, str] = {}
        if json_body:
            headers["Content-Type"] = "application/json"
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def login(self, username: str = "", password: str = "") -> str:
        user = username or os.environ.get("CAP_USER", "")
        pwd = password or os.environ.get("CAP_PASSWORD", "")
        if not user or not pwd:
            raise CapabilityLayerError(
                "缺少凭据：请通过 --token 提供，或设置 CAP_TOKEN / CAP_USER + CAP_PASSWORD"
            )
        r = httpx.post(
            f"{self.base_url}/api/auth/login",
            json={"username": user, "password": pwd},
            timeout=30,
        )
        r.raise_for_status()
        self.token = r.json()["access_token"]
        return self.token

    def ensure_token(self, username: str = "", password: str = "") -> str:
        if self.token:
            return self.token
        return self.login(username, password)

    # ---------- 能力目录与下载 ----------

    def sync(self) -> list[dict[str, Any]]:
        """拉取各能力的最新发布版本目录。"""
        r = httpx.get(
            f"{self.base_url}/api/capabilities/sync",
            headers=self._headers(),
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def find_in_catalog(self, name: str, cap_type: str = "") -> dict[str, Any] | None:
        for item in self.sync():
            if item.get("name") != name:
                continue
            if cap_type and item.get("type") != cap_type:
                continue
            return item
        return None

    def download(self, name: str, version: str = "") -> tuple[bytes, dict[str, str]]:
        """下载能力包 zip，返回 (内容, 响应头)。"""
        url = f"{self.base_url}/api/capabilities/{quote(name, safe='')}/download"
        if version:
            url += f"?version={quote(version, safe='')}"
        r = httpx.get(url, headers=self._headers(), timeout=120)
        r.raise_for_status()
        return r.content, dict(r.headers)

    # ---------- 云端 runtime（HTTP 直调） ----------

    def run_agent_cloud(self, name: str, task: str) -> dict[str, Any]:
        self.ensure_token()
        r = httpx.post(
            f"{self.base_url}/api/runtime/agents/{quote(name, safe='')}/tasks",
            headers=self._headers(json_body=True),
            json={"task": task},
            timeout=600,
        )
        r.raise_for_status()
        return r.json()

    def invoke_tool_cloud(self, name: str, params: dict[str, Any]) -> dict[str, Any]:
        self.ensure_token()
        r = httpx.post(
            f"{self.base_url}/api/runtime/tools/{quote(name, safe='')}/invoke",
            headers=self._headers(json_body=True),
            json={"params": params},
            timeout=120,
        )
        r.raise_for_status()
        return r.json()

    # ---------- A2A 客户端 ----------

    def discover_agents(self) -> list[dict[str, Any]]:
        """发现平台内所有可互调 Agent（Agent Card 列表）。"""
        self.ensure_token()
        r = httpx.get(
            f"{self.base_url}/api/a2a/agents",
            headers=self._headers(),
            timeout=30,
        )
        r.raise_for_status()
        return r.json()

    def find_agent_card(self, name: str) -> dict[str, Any]:
        cards = self.discover_agents()
        for card in cards:
            if card.get("name") == name:
                return card
        raise CapabilityLayerError(f"云端不存在可互调的 Agent：{name}")

    def a2a_send(self, agent_url: str, text: str, task_id: str = "") -> dict[str, Any]:
        """A2A JSON-RPC tasks/send（同步等待结果）。"""
        self.ensure_token()
        payload = {
            "jsonrpc": "2.0",
            "id": f"cap-{uuid.uuid4().hex[:12]}",
            "method": "tasks/send",
            "params": {
                "id": task_id or f"cap-{uuid.uuid4().hex[:12]}",
                "message": {"role": "user", "parts": [{"type": "text", "text": text}]},
                "metadata": {"source": "capability-layer-consumer"},
            },
        }
        r = httpx.post(
            agent_url, headers=self._headers(json_body=True), json=payload, timeout=600
        )
        r.raise_for_status()
        return r.json()

    def a2a_get(self, agent_url: str, task_id: str) -> dict[str, Any]:
        self.ensure_token()
        payload = {
            "jsonrpc": "2.0",
            "id": f"cap-{uuid.uuid4().hex[:12]}",
            "method": "tasks/get",
            "params": {"id": task_id},
        }
        r = httpx.post(
            agent_url, headers=self._headers(json_body=True), json=payload, timeout=60
        )
        r.raise_for_status()
        return r.json()

    def a2a_cancel(self, agent_url: str, task_id: str, reason: str = "") -> dict[str, Any]:
        self.ensure_token()
        payload = {
            "jsonrpc": "2.0",
            "id": f"cap-{uuid.uuid4().hex[:12]}",
            "method": "tasks/cancel",
            "params": {"id": task_id, "reason": reason},
        }
        r = httpx.post(
            agent_url, headers=self._headers(json_body=True), json=payload, timeout=60
        )
        r.raise_for_status()
        return r.json()
