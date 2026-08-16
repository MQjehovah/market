"""本地 A2A 服务端：把本地已安装的 Agent 暴露为标准 A2A Agent。

云端 Agent 或其它本地 Agent 可通过 A2A JSON-RPC 反向调用本机 Agent：
    GET  /.well-known/agent-card.json       A2A 标准发现（元卡片）
    GET  /api/a2a/agents                    已安装 Agent 卡片列表
    GET  /api/a2a/agents/{name}/card        单个卡片
    POST /api/a2a/agents/{name}/a2a         JSON-RPC tasks/send|get|cancel
    GET  /api/a2a/tasks/{id}                REST 查询任务状态
    GET  /health                            健康检查

仅依赖标准库（http.server + json），任务持久化在 <state-dir>/tasks.jsonl。
"""

from __future__ import annotations

import json
import os
import re
import threading
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import quote, unquote, urlparse

from .local_runner import run_local

TERMINAL_STATES = {"completed", "failed", "canceled", "rejected"}


class TaskStore:
    """本地任务存储（JSONL 追加写，进程内缓存 + 磁盘持久化）。"""

    def __init__(self, state_dir: str | Path):
        self.path = Path(state_dir) / "tasks.jsonl"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._tasks: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._load()

    def _load(self) -> None:
        if not self.path.is_file():
            return
        for line in self.path.read_text(encoding="utf-8", errors="replace").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                task = json.loads(line)
            except ValueError:
                continue
            self._tasks[task["id"]] = task

    def save(self, task: dict[str, Any]) -> None:
        with self._lock:
            self._tasks[task["id"]] = task
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(task, ensure_ascii=False) + "\n")

    def get(self, task_id: str) -> dict[str, Any] | None:
        return self._tasks.get(task_id)


def load_installed_agents(config_dir: str) -> list[dict[str, Any]]:
    """扫描 config/agents/*/installed.json（缺失时回退读取 PROMPT.md frontmatter）。"""
    agents_dir = Path(config_dir) / "agents"
    agents: list[dict[str, Any]] = []
    if not agents_dir.is_dir():
        return agents
    for d in sorted(agents_dir.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        manifest_file = d / "installed.json"
        if manifest_file.is_file():
            try:
                manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
            except (ValueError, OSError):
                manifest = {}
            agents.append(
                {
                    "name": manifest.get("name") or d.name,
                    "version": manifest.get("version") or "0.0.0",
                    "description": manifest.get("description")
                    or f"本地 Agent {d.name}（由能力层组装）",
                    "dir": str(d),
                }
            )
            continue
        prompt_file = d / "PROMPT.md"
        if not prompt_file.is_file():
            continue
        text = prompt_file.read_text(encoding="utf-8", errors="replace")
        name, description = _frontmatter(text, d.name)
        agents.append(
            {
                "name": name,
                "version": "0.0.0",
                "description": description or f"本地 Agent {d.name}",
                "dir": str(d),
            }
        )
    return agents


def _frontmatter(text: str, fallback: str) -> tuple[str, str]:
    if not text.startswith("---"):
        return fallback, ""
    end = text.find("\n---", 3)
    if end <= 0:
        return fallback, ""
    meta: dict[str, str] = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta.get("name") or fallback, meta.get("description") or ""


def build_card(agent: dict[str, Any], base_url: str) -> dict[str, Any]:
    name = agent["name"]
    url = f"{base_url}/api/a2a/agents/{quote(name, safe='')}"
    return {
        "protocolVersion": "1.0",
        "name": name,
        "description": agent["description"],
        "url": url,
        "version": agent["version"],
        "provider": {"organization": "本地能力层消费者"},
        "documentationUrl": f"{base_url}/api/a2a/agents/{quote(name, safe='')}/card",
        "capabilities": {
            "streaming": False,
            "pushNotifications": False,
            "stateTransitionHistory": True,
        },
        "securitySchemes": {},
        "security": [],
        "defaultInputModes": ["text"],
        "defaultOutputModes": ["text"],
        "skills": [
            {
                "id": name,
                "name": name,
                "description": agent["description"],
                "examples": [f"委派任务给本地 Agent {name}"],
            }
        ],
    }


def _extract_text(message: dict[str, Any] | None) -> str:
    if not message:
        return ""
    texts = []
    for part in message.get("parts") or []:
        if isinstance(part, dict) and part.get("text"):
            texts.append(str(part["text"]))
    return "\n".join(texts)


class A2AHandler(BaseHTTPRequestHandler):
    server_version = "CapabilityLayerConsumer/0.1"

    def _send(self, status: int, body: dict[str, Any]) -> None:
        payload = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _json_error(self, status: int, message: str) -> None:
        self._send(status, {"error": message})

    def _read_json(self) -> dict[str, Any] | None:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return None
        try:
            return json.loads(self.rfile.read(length).decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return None

    # ---------- 路由 ----------

    def do_GET(self):  # noqa: N802
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/health":
            return self._send(200, {"status": "ok", "service": "cap-local-a2a"})
        if path == "/.well-known/agent-card.json":
            return self._meta_card()
        if path == "/api/a2a/agents":
            return self._agent_list()
        m = re.fullmatch(r"/api/a2a/agents/([^/]+)/card", path)
        if m:
            return self._agent_card(unquote(m.group(1)))
        m = re.fullmatch(r"/api/a2a/tasks/([^/]+)", path)
        if m:
            task = self.server.store.get(unquote(m.group(1)))
            if task is None:
                return self._json_error(404, "任务不存在")
            return self._send(200, task)
        return self._json_error(404, f"未找到路径：{path}")

    def do_POST(self):  # noqa: N802
        parsed = urlparse(self.path)
        m = re.fullmatch(r"/api/a2a/agents/([^/]+)/a2a", parsed.path)
        if not m:
            return self._json_error(404, f"未找到路径：{parsed.path}")
        agent_name = unquote(m.group(1))
        req = self._read_json()
        if not req:
            return self._json_error(400, "请求体不是合法 JSON")
        return self._handle_jsonrpc(agent_name, req)

    # ---------- A2A ----------

    def _meta_card(self) -> None:
        agents = load_installed_agents(self.server.config_dir)
        base_url = self._base_url()
        card = {
            "protocolVersion": "1.0",
            "name": "本地能力层（cap serve）",
            "description": "聚合本地已安装 Agent 的统一入口：发现 Agent Card 并委派任务。",
            "url": f"{base_url}/api/a2a",
            "version": "0.1.0",
            "provider": {"organization": "本地能力层消费者"},
            "capabilities": {
                "streaming": False,
                "pushNotifications": False,
                "stateTransitionHistory": True,
            },
            "securitySchemes": {},
            "security": [],
            "defaultInputModes": ["text"],
            "defaultOutputModes": ["text"],
            "skills": [
                {
                    "id": a["name"],
                    "name": a["name"],
                    "description": a["description"],
                    "examples": [f"委派任务给本地 Agent {a['name']}"],
                }
                for a in agents
            ],
        }
        self._send(200, card)

    def _agent_list(self) -> None:
        base_url = self._base_url()
        cards = [build_card(a, base_url) for a in load_installed_agents(self.server.config_dir)]
        self._send(200, cards)

    def _agent_card(self, name: str) -> None:
        agent = next((a for a in load_installed_agents(self.server.config_dir) if a["name"] == name), None)
        if agent is None:
            return self._json_error(404, f"本地未安装 Agent：{name}")
        self._send(200, build_card(agent, self._base_url()))

    def _base_url(self) -> str:
        host = self.server.host
        port = self.server.server_port
        return f"http://{host}:{port}"

    def _handle_jsonrpc(self, agent_name: str, req: dict[str, Any]) -> None:
        method = req.get("method", "")
        req_id = req.get("id")
        params = req.get("params") or {}
        if method == "tasks/send":
            self._task_send(agent_name, req_id, params)
        elif method == "tasks/get":
            self._task_get(req_id, params)
        elif method == "tasks/cancel":
            self._task_cancel(req_id, params)
        else:
            self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32601, "message": f"不支持的方法：{method}"},
                },
            )

    def _task_send(self, agent_name: str, req_id, params: dict[str, Any]) -> None:
        agents = load_installed_agents(self.server.config_dir)
        agent = next((a for a in agents if a["name"] == agent_name), None)
        if agent is None:
            return self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32003, "message": f"本地未安装 Agent：{agent_name}"},
                },
            )
        text = _extract_text(params.get("message"))
        task_id = str(params.get("id") or f"cap-{uuid.uuid4().hex[:12]}")
        task = {
            "id": task_id,
            "status": {"state": "submitted", "message": None},
            "artifacts": [],
            "history": [params.get("message")] if params.get("message") else [],
            "metadata": params.get("metadata") or {},
        }
        self.server.store.save(task)

        result = run_local(
            agent_name,
            text or "（无文本内容）",
            config_dir=self.server.config_dir,
            agent_root=self.server.agent_root,
            workspace=self.server.workspace,
            timeout=self.server.task_timeout,
        )
        if result.get("ok"):
            task["status"] = {
                "state": "completed",
                "message": {"role": "agent", "parts": [{"type": "text", "text": result.get("output") or ""}]},
            }
            task["artifacts"] = [
                {
                    "name": "task_result",
                    "description": f"本地 Agent {agent_name} 的执行结果",
                    "parts": [{"type": "text", "text": result.get("output") or ""}],
                    "index": 0,
                }
            ]
            task["history"].append(
                {"role": "agent", "parts": [{"type": "text", "text": result.get("output") or ""}]}
            )
            task["metadata"]["mode"] = "local"
        else:
            err = result.get("error") or result.get("output") or "本地执行失败"
            task["status"] = {
                "state": "failed",
                "message": {"role": "agent", "parts": [{"type": "text", "text": f"执行失败：{err}"}]},
            }
            task["metadata"]["mode"] = "local"
            task["metadata"]["error"] = err
        task["metadata"]["source"] = "a2a"
        self.server.store.save(task)
        self._send(
            200,
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": task,
            },
        )

    def _task_get(self, req_id, params: dict[str, Any]) -> None:
        task = self.server.store.get(str(params.get("id", "")))
        if task is None:
            return self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32001, "message": "任务不存在"},
                },
            )
        self._send(200, {"jsonrpc": "2.0", "id": req_id, "result": task})

    def _task_cancel(self, req_id, params: dict[str, Any]) -> None:
        task = self.server.store.get(str(params.get("id", "")))
        if task is None:
            return self._send(
                200,
                {
                    "jsonrpc": "2.0",
                    "id": req_id,
                    "error": {"code": -32001, "message": "任务不存在"},
                },
            )
        if task["status"]["state"] not in TERMINAL_STATES:
            task["status"]["state"] = "canceled"
            task["status"]["message"] = {
                "role": "agent",
                "parts": [{"type": "text", "text": f"任务已取消：{params.get('reason') or '由请求方取消'}"}],
            }
            self.server.store.save(task)
        self._send(200, {"jsonrpc": "2.0", "id": req_id, "result": task})

    def log_message(self, fmt: str, *args) -> None:  # noqa: N802
        print(f"[cap-serve] {fmt % args}")


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8765,
    config_dir: str,
    agent_root: str = "",
    workspace: str = "",
    state_dir: str = "",
    task_timeout: int = 900,
) -> None:
    server = ThreadingHTTPServer((host, port), A2AHandler)
    server.config_dir = os.path.abspath(config_dir)
    server.agent_root = agent_root
    server.workspace = workspace
    server.state_dir = os.path.abspath(state_dir or os.path.join(os.path.expanduser("~"), ".cap"))
    server.task_timeout = task_timeout
    server.store = TaskStore(server.state_dir)
    server.host = host
    agents = load_installed_agents(config_dir)
    print(
        f"[cap-serve] 本地 A2A 服务已启动：http://{host}:{port}  "
        f"（已安装 {len(agents)} 个 Agent：{', '.join(a['name'] for a in agents) or '无'}）"
    )
    print(f"[cap-serve] 发现卡片：http://{host}:{port}/.well-known/agent-card.json")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[cap-serve] 已停止")
