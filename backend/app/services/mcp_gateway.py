"""MCP HTTP 中转网关。

两个方向：
1) 出站：统一的 MCP 客户端连接器（stdio / streamable_http / sse），
   供 Agent 运行时（MCPBridge）与 MCP 能力包使用 —— 平台作为 MCP 客户端，
   通过 HTTP 连接任意语言的远程 MCP server，不再要求容器内安装对应运行时。
2) 入站：把注册的 MCP 服务（stdio 子进程或远程 HTTP/SSE）统一暴露为 HTTP 端点
   （SSE + Streamable HTTP），外部 MCP 客户端（Dify、Claude Desktop、其他 Agent）
   可以像连接普通 MCP server 一样调用，stdio 服务自动转成 HTTP（mcp-proxy 模式）。

端点约定（挂在 /api/mcp-gateway 下）：
    GET  /{name}/sse        SSE 传输的 MCP 服务（GET 建立连接）
    POST /{name}/messages   SSE 客户端回传 JSON-RPC 消息
    GET/POST/DELETE /{name}/stream   Streamable HTTP 传输的 MCP 服务
"""

import asyncio
import hashlib
import hmac
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import threading
import time
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Any, Awaitable, Callable

import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client
from mcp.server.lowlevel import Server as MCPServer
from mcp.server.sse import SseServerTransport
from mcp.server.streamable_http_manager import StreamableHTTPSessionManager
from mcp.server.models import InitializationOptions
from mcp.types import ServerCapabilities
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("market.mcp_gateway")

ENV_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
CONNECT_TIMEOUT = 30
CALL_TIMEOUT = 120
_GENERIC_PYTHON = {"python", "python3", "py"}

ConfigLoader = Callable[[str], Awaitable[dict[str, Any] | None]]


def format_connect_error(exc: BaseException, stderr: str = "") -> str:
    """展开 ExceptionGroup，并附上 stdio 子进程 stderr（常见缺依赖场景）。"""
    if isinstance(exc, BaseExceptionGroup):
        parts = [format_connect_error(e) for e in exc.exceptions]
        msg = "; ".join(p for p in parts if p) or str(exc)
    else:
        msg = str(exc).strip() or type(exc).__name__
    err = (stderr or "").strip()
    if err:
        # 只保留末尾，避免把整段日志塞进 API 响应
        tail = err[-1200:]
        msg = f"{msg}\n--- stderr ---\n{tail}"
    return msg[:2000]


def read_package_files(cap) -> dict[str, bytes]:
    """读取能力包 zip 内全部文件（含 connection.json / implementation/）。"""
    if not getattr(cap, "artifacts", None):
        return {}
    try:
        from app.storage import get_storage

        content = get_storage().open(cap.artifacts[-1].uri).read()
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return {name: zf.read(name) for name in zf.namelist() if not name.endswith("/")}
    except Exception as exc:  # noqa: BLE001
        logger.warning("读取能力包失败 cap=%s: %s", getattr(cap, "name", "?"), exc)
        return {}


def extract_implementation(tmpdir: Path, package_files: dict[str, bytes]) -> bool:
    """把 implementation/** 解到临时目录，保留相对目录结构。返回是否解出文件。"""
    extracted = False
    for fname, data in package_files.items():
        if not fname.startswith("implementation/") or fname.endswith("/"):
            continue
        rel = fname[len("implementation/") :]
        if not rel or ".." in Path(rel).parts:
            continue
        dest = tmpdir / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        extracted = True
    return extracted


def rewrite_stdio_script_arg(tmpdir: Path, arg: str) -> str:
    """相对脚本路径 → 临时目录：优先 implementation 相对路径，再 basename / 唯一匹配。"""
    if arg.startswith(("-", "--")) or Path(arg).is_absolute():
        return arg
    if Path(arg).suffix not in (".py", ".js", ".mjs"):
        return arg
    if arg.startswith("implementation/"):
        cand = tmpdir / arg[len("implementation/") :]
        if cand.is_file():
            return str(cand)
    by_name = tmpdir / Path(arg).name
    if by_name.is_file():
        return str(by_name)
    matches = [p for p in tmpdir.rglob(Path(arg).name) if p.is_file()]
    if len(matches) == 1:
        return str(matches[0])
    return arg


def resolve_placeholders(value: Any) -> Any:
    """递归解析 ${VAR} / ${VAR:default} 占位符（env/headers/args 中的敏感信息）。"""

    def _sub(m: re.Match) -> str:
        full = m.group(1).strip()
        var, _, default = full.partition(":")
        real = os.environ.get(var, "")
        if not real and default:
            real = default
        return real

    if isinstance(value, dict):
        return {k: resolve_placeholders(v) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(v) for v in value]
    if isinstance(value, str):
        return ENV_PLACEHOLDER.sub(_sub, value)
    return value


def sanitize(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_-]", "-", name).strip("-")[:48] or "mcp"


def row_to_config(row) -> dict[str, Any]:
    """数据库行 → 配置字典（headers/env 存的是 JSON，转为 dict）。"""
    headers = row.headers or {}
    env = row.env or {}
    args = row.args or []
    if isinstance(headers, str):
        try:
            headers = json.loads(headers)
        except ValueError:
            headers = {}
    if isinstance(env, str):
        try:
            env = json.loads(env)
        except ValueError:
            env = {}
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            args = []
    return {
        "name": row.name,
        "description": row.description or "",
        "transport": row.transport,
        "url": row.url or "",
        "headers": headers,
        "command": row.command or "",
        "args": args,
        "env": env,
        "cwd": row.cwd or "",
        "api_token": row.api_token or "",
        "capability_id": row.capability_id or "",
        "enabled": bool(row.enabled),
    }


@asynccontextmanager
async def connect_upstream(
    config: dict[str, Any], package_files: dict[str, bytes] | None = None
):
    """按配置连接上游 MCP server，返回已初始化的 ClientSession。"""
    transport = (config.get("transport") or "stdio").lower()
    cleanup: list[Path] = []
    errlog = None

    try:
        if transport == "stdio":
            command = config.get("command") or "python"
            # 能力包里常见 "python"；用当前解释器，保证与平台同环境依赖一致
            if str(command).strip().lower() in _GENERIC_PYTHON:
                command = sys.executable
            args = list(config.get("args") or [])
            env = resolve_placeholders(config.get("env") or {})
            cwd: str | None = config.get("cwd") or None
            if package_files:
                tmpdir = Path(tempfile.mkdtemp(prefix=f"mcp-gw-{sanitize(config['name'])}-"))
                cleanup.append(tmpdir)
                if extract_implementation(tmpdir, package_files):
                    cwd = str(tmpdir)
                    args = [rewrite_stdio_script_arg(tmpdir, a) for a in args]
            merged_env = dict(os.environ)
            merged_env["PYTHONUNBUFFERED"] = "1"
            merged_env.update(env)
            params = StdioServerParameters(command=command, args=args, env=merged_env, cwd=cwd)
            # 真实文件描述符：anyio/subprocess 不能把 stderr 接到 StringIO
            errlog = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")

            def _stderr_text() -> str:
                try:
                    errlog.seek(0)
                    return errlog.read()
                except Exception:  # noqa: BLE001
                    return ""

            try:
                async with asyncio.timeout(CONNECT_TIMEOUT):
                    stdio = stdio_client(params, errlog=errlog)
                    read, write = await stdio.__aenter__()
                    try:
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            yield session
                    finally:
                        await stdio.__aexit__(*sys.exc_info())
            except (asyncio.CancelledError, GeneratorExit):
                raise
            except BaseException as exc:
                raise RuntimeError(format_connect_error(exc, _stderr_text())) from None

        elif transport in ("http", "streamable_http"):
            url = config.get("url") or ""
            if not url:
                raise RuntimeError("http 传输需要配置 url")
            headers = resolve_placeholders(config.get("headers") or {})
            timeout = httpx.Timeout(
                connect=CONNECT_TIMEOUT, read=CALL_TIMEOUT, write=CALL_TIMEOUT, pool=CALL_TIMEOUT
            )
            async with asyncio.timeout(CONNECT_TIMEOUT):
                client = httpx.AsyncClient(headers=headers, timeout=timeout)
                try:
                    async with streamable_http_client(url, http_client=client) as streams:
                        read, write = streams[0], streams[1]
                        async with ClientSession(read, write) as session:
                            await session.initialize()
                            yield session
                finally:
                    await client.aclose()

        elif transport == "sse":
            url = config.get("url") or ""
            if not url:
                raise RuntimeError("sse 传输需要配置 url")
            headers = resolve_placeholders(config.get("headers") or {})
            async with asyncio.timeout(CONNECT_TIMEOUT):
                async with sse_client(
                    url, headers=headers, timeout=CONNECT_TIMEOUT, sse_read_timeout=CALL_TIMEOUT
                ) as streams:
                    read, write = streams[0], streams[1]
                    async with ClientSession(read, write) as session:
                        await session.initialize()
                        yield session
        else:
            raise RuntimeError(f"未知传输类型 {transport}")
    finally:
        if errlog is not None:
            try:
                errlog.close()
            except Exception:  # noqa: BLE001
                pass
        for path in cleanup:
            shutil.rmtree(path, ignore_errors=True)


async def probe_tools(config: dict[str, Any], package_files: dict[str, bytes] | None = None) -> list[dict[str, Any]]:
    """连接并列出上游工具（管理后台测试 / 工作流 mcp 节点探测用）。"""
    tools: list[dict[str, Any]] = []
    async with connect_upstream(config, package_files) as session:
        result = await session.list_tools()
        for tool in result.tools:
            tools.append(
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "inputSchema": tool.inputSchema,
                }
            )
    return tools


async def load_gateway_config_by_name(db, name: str) -> dict[str, Any] | None:
    """按服务名从数据库读取网关配置（供 Agent 运行时解析 gateway 传输）。"""
    from sqlalchemy import select

    from app.models import MCPGatewayServer

    row = await db.scalar(select(MCPGatewayServer).where(MCPGatewayServer.name == name))
    return row_to_config(row) if row is not None else None


def make_forward_server(name: str, upstream: ClientSession) -> MCPServer:
    """构建转发型低层 MCP server：tools/list 与 tools/call 全部转发到上游。"""

    server = MCPServer(f"gateway-{name}")

    @server.list_tools()
    async def list_tools():
        result = await upstream.list_tools()
        return result.tools

    @server.call_tool()
    async def call_tool(tool_name: str, arguments: dict[str, Any]):
        return await upstream.call_tool(tool_name, arguments or {})

    return server


class _ServerProxy:
    """StreamableHTTPSessionManager 使用的“按会话建上游”的 server 代理。

    manager 每个客户端会话会调用一次 run()，我们在该会话内连接独立的上游，
    保证 stdio 子进程 / HTTP 会话的生命周期与客户端会话一致。
    """

    def __init__(self, name: str, loader: ConfigLoader) -> None:
        self.name = name
        self._loader = loader

    def create_initialization_options(self, **kwargs) -> InitializationOptions:
        return InitializationOptions(
            server_name=f"gateway-{self.name}",
            server_version="1.0.0",
            capabilities=ServerCapabilities(tools={}),
        )

    async def run(
        self,
        read_stream,
        write_stream,
        initialization_options: InitializationOptions,
        *,
        raise_exceptions: bool = False,
        stateless: bool = False,
    ) -> None:
        config = await self._loader(self.name)
        if config is None or not config.get("enabled", True):
            raise RuntimeError(f"网关服务 {self.name} 不存在或已停用")
        async with connect_upstream(config) as upstream:
            server = make_forward_server(self.name, upstream)
            await server.run(
                read_stream,
                write_stream,
                initialization_options,
                raise_exceptions=raise_exceptions,
                stateless=stateless,
            )


class GatewayEndpoints:
    """单个网关服务的入站端点：SSE + Streamable HTTP + 消息接收。"""

    def __init__(self, name: str, loader: ConfigLoader) -> None:
        self.name = name
        self._loader = loader
        self.sse = SseServerTransport(endpoint=f"/{name}/messages/")
        self._stream_manager: StreamableHTTPSessionManager | None = None
        self._stream_task: asyncio.Task | None = None
        self._stream_ready = asyncio.Event()

    async def ensure_stream_manager(self) -> None:
        if self._stream_task is None:
            self._stream_task = asyncio.create_task(self._run_stream_manager())
        await self._stream_ready.wait()

    async def _run_stream_manager(self) -> None:
        manager = StreamableHTTPSessionManager(
            _ServerProxy(self.name, self._loader), json_response=True
        )
        self._stream_manager = manager
        try:
            async with manager.run():
                self._stream_ready.set()
                await asyncio.Future()  # 常驻，直到取消
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            logger.exception("网关服务 %s 的 Streamable HTTP 管理器异常退出", self.name)
            self._stream_ready.set()

    async def handle_sse(self, scope, receive, send, config: dict[str, Any]) -> None:
        async with self.sse.connect_sse(scope, receive, send) as streams:
            async with connect_upstream(config) as upstream:
                server = make_forward_server(self.name, upstream)
                await server.run(
                    streams[0], streams[1], server.create_initialization_options()
                )

    async def handle_messages(self, scope, receive, send) -> None:
        await self.sse.handle_post_message(scope, receive, send)

    async def handle_stream(self, scope, receive, send) -> None:
        await self.ensure_stream_manager()
        await self._stream_manager.handle_request(scope, receive, send)

    async def shutdown(self) -> None:
        if self._stream_task is not None:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._stream_task = None


class GatewayRegistry:
    """按服务名持有入站端点实例。"""

    def __init__(self, loader: ConfigLoader) -> None:
        self._loader = loader
        self._endpoints: dict[str, GatewayEndpoints] = {}

    def get(self, name: str) -> GatewayEndpoints:
        ep = self._endpoints.get(name)
        if ep is None:
            ep = GatewayEndpoints(name, self._loader)
            self._endpoints[name] = ep
        return ep

    async def shutdown(self) -> None:
        for ep in self._endpoints.values():
            await ep.shutdown()
        self._endpoints.clear()


@dataclass
class GatewayIdentity:
    """入站请求身份：来源 server_token | jwt | sso | anonymous。"""

    source: str
    user_id: str | None = None
    username: str = ""
    role: str = ""
    user: Any = None  # ORM User；仅 jwt/sso 来源非空


class GatewayAuthError(Exception):
    """网关鉴权失败（对应 401）。"""


def _header(scope, name: str) -> str:
    """从 ASGI scope 读首个同名 header（name 需小写）。"""
    for key, value in scope.get("headers") or []:
        if key.decode("latin-1").lower() == name:
            return value.decode("latin-1")
    return ""


def _bearer_token(scope) -> str:
    auth = _header(scope, "authorization")
    if auth.lower().startswith("bearer "):
        return auth[7:].strip()
    return ""


async def _resolve_bearer_identity(token: str) -> GatewayIdentity | None:
    """Bearer → 本地 JWT / SSO 身份；均不匹配返回 None（短会话，无依赖注入）。"""
    from app.auth import resolve_user_by_bearer
    from app.database import SessionLocal

    try:
        async with SessionLocal() as db:
            resolved = await resolve_user_by_bearer(db, token)
    except Exception as exc:  # noqa: BLE001
        logger.warning("网关 Bearer 解析失败: %s", exc)
        return None
    if resolved is None:
        return None
    user, source = resolved
    return GatewayIdentity(
        source=source,
        user_id=user.id,
        username=user.username,
        role=user.role,
        user=user,
    )


async def authenticate_request(config: dict[str, Any], scope) -> GatewayIdentity:
    """网关端点鉴权：per-server token → 本地 JWT → SSO → 匿名/401。

    1) api_token 非空时，X-Gateway-Token / Bearer 精确匹配（原行为，文案不变）；
    2) Bearer 依次尝试本地 HS256 JWT 与 SSO RS256（复用 auth 双轨解析）；
    3) 都不匹配：mcp_gateway_require_token=True → GatewayAuthError；False → 匿名。
    """
    from app.config import get_settings

    expected = config.get("api_token") or ""
    if expected:
        candidates = (_header(scope, "x-gateway-token"), _bearer_token(scope))
        if any(c and hmac.compare_digest(c, expected) for c in candidates):
            return GatewayIdentity(source="server_token")
    bearer = _bearer_token(scope)
    if bearer:
        identity = await _resolve_bearer_identity(bearer)
        if identity is not None:
            return identity
    if expected or get_settings().mcp_gateway_require_token:
        raise GatewayAuthError("网关令牌无效")
    return GatewayIdentity(source="anonymous")


RATE_WINDOW_SECONDS = 60.0
_rate_hits: dict[str, list[float]] = {}
_rate_lock = threading.Lock()
_rate_last_cleanup = 0.0


def rate_limit_key(identity: GatewayIdentity, config: dict[str, Any], scope) -> str:
    """限流身份键：登录用户 → 网关令牌指纹 → 客户端 IP。"""
    if identity.user_id:
        return f"user:{identity.user_id}"
    token = config.get("api_token") or ""
    if token:
        return "token:" + hashlib.sha256(token.encode("utf-8")).hexdigest()[:16]
    client = scope.get("client") or ("", 0)
    return f"ip:{client[0]}"


def check_rate_limit(key: str, limit: int, now: float | None = None) -> tuple[bool, int]:
    """60s 滑动窗口限流（纯函数）。limit<=0 关闭；返回 (allowed, retry_after 秒)。"""
    global _rate_last_cleanup

    if limit <= 0:
        return True, 0
    now = time.monotonic() if now is None else now
    with _rate_lock:
        hits = [t for t in _rate_hits.get(key, []) if now - t < RATE_WINDOW_SECONDS]
        if len(hits) >= limit:
            _rate_hits[key] = hits
            return False, max(1, ceil(RATE_WINDOW_SECONDS - (now - hits[0])))
        hits.append(now)
        _rate_hits[key] = hits
        if now - _rate_last_cleanup >= RATE_WINDOW_SECONDS:
            _rate_last_cleanup = now
            for stale in [k for k, v in _rate_hits.items() if not v or now - v[-1] >= RATE_WINDOW_SECONDS]:
                _rate_hits.pop(stale, None)
        return True, 0


def reset_rate_limits() -> None:
    """清空滑动窗口状态（测试 / 本地重置用）。"""
    global _rate_last_cleanup

    with _rate_lock:
        _rate_hits.clear()
        _rate_last_cleanup = 0.0


_METHOD_NAMES = {"initialize": "initialize", "tools/list": "tools_list", "tools/call": "tools_call"}


@dataclass
class MCPCall:
    """审计用的单条 JSON-RPC 消息摘要。"""

    method: str = "other"
    tool: str = ""
    rpc_id: Any = None


def parse_mcp_calls(body: bytes) -> list[MCPCall]:
    """解析请求体中的 JSON-RPC 消息（支持 batch）：initialize/tools_list/tools_call/other。"""
    if not body:
        return []
    try:
        data = json.loads(body.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        return []
    items = data if isinstance(data, list) else [data]
    calls: list[MCPCall] = []
    for item in items:
        if not isinstance(item, dict) or "method" not in item:
            continue
        method = str(item.get("method") or "")
        params = item.get("params") if isinstance(item.get("params"), dict) else {}
        tool = str(params.get("name") or "") if method == "tools/call" else ""
        calls.append(
            MCPCall(method=_METHOD_NAMES.get(method, "other"), tool=tool[:255], rpc_id=item.get("id"))
        )
    return calls


def _content_text(content: Any) -> str:
    if not isinstance(content, list):
        return ""
    parts = [
        str(item.get("text") or "")
        for item in content
        if isinstance(item, dict) and item.get("type") == "text"
    ]
    return " ".join(p for p in parts if p).strip()


def extract_rpc_error(status: int | None, body: bytes) -> str:
    """从 HTTP 响应提取错误：JSON-RPC error / result.isError / 非 2xx 状态。"""
    try:
        data = json.loads(body.decode("utf-8")) if body else None
    except (ValueError, UnicodeDecodeError):
        data = None
    items = data if isinstance(data, list) else [data]
    for item in items:
        if not isinstance(item, dict):
            continue
        err = item.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err)[:300]
        result = item.get("result")
        if isinstance(result, dict) and result.get("isError"):
            return (_content_text(result.get("content")) or "工具调用失败")[:300]
    if status is not None and status >= 400:
        if isinstance(data, dict):
            return str(data.get("detail") or data)[:300]
        return (body.decode("utf-8", "replace") if body else f"HTTP {status}")[:300]
    return ""


async def record_gateway_calls(
    server_name: str,
    config: dict[str, Any],
    identity: GatewayIdentity,
    calls: list[MCPCall],
    conversation_id: str,
    duration_ms: int,
    ok: bool,
    error: str = "",
) -> None:
    """能力级审计 + 用量：每条 JSON-RPC 消息一行；写失败仅告警不阻断请求。"""
    if not calls:
        return
    from app.database import SessionLocal
    from app.models import Capability, MCPGatewayCall, User
    from app.services.marketplace import record_usage

    error = (error or "")[:300]
    try:
        async with SessionLocal() as db:
            cap = None
            cap_id = config.get("capability_id") or ""
            if cap_id:
                cap = await db.get(Capability, cap_id)
            usable = cap is not None and cap.status in ("published", "deprecated", "reviewing")
            for call in calls:
                db.add(
                    MCPGatewayCall(
                        server_name=server_name,
                        capability_id=cap_id,
                        capability_version=cap.version if cap is not None else "",
                        user_id=identity.user_id or "",
                        username=identity.username,
                        source=identity.source,
                        method=call.method,
                        tool=call.tool,
                        conversation_id=conversation_id,
                        duration_ms=duration_ms,
                        ok=ok,
                        error=error,
                    )
                )
                if identity.user_id and usable and call.method == "tools_call":
                    try:
                        user = await db.get(User, identity.user_id)
                        if user is not None:
                            await record_usage(
                                db,
                                user,
                                cap,
                                "mcp_call",
                                {"tool": call.tool, "server": server_name},
                                result_status="ok" if ok else "failed",
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("MCP 网关用量写入失败 server=%s: %s", server_name, exc)
            await db.commit()
    except Exception as exc:  # noqa: BLE001
        logger.warning("MCP 网关审计写入失败 server=%s: %s", server_name, exc)


class _ResponseCapture:
    """包装 send 捕获状态码与响应体（仅供审计，不改变转发行为）。"""

    def __init__(self, send: Callable) -> None:
        self._send = send
        self.status: int | None = None
        self.body = bytearray()
        self.started = False

    async def __call__(self, message: dict) -> None:
        mtype = message.get("type")
        if mtype == "http.response.start":
            self.status = int(message.get("status") or 0)
            self.started = True
        elif mtype == "http.response.body":
            self.body.extend(message.get("body") or b"")
        await self._send(message)


async def _read_body(receive) -> tuple[bytes, Callable[[], Awaitable[dict]]]:
    """先读完请求体再重放给下游（MCP JSON-RPC 体量小），供解析审计。"""
    pending: list[dict] = []
    chunks: list[bytes] = []
    while True:
        message = await receive()
        pending.append(message)
        if message.get("type") != "http.request":
            break
        chunks.append(message.get("body") or b"")
        if not message.get("more_body", False):
            break

    async def replay() -> dict:
        if pending:
            return pending.pop(0)
        return await receive()

    return b"".join(chunks), replay


async def send_json(scope, receive, send, status: int, body: dict) -> None:
    response = JSONResponse(body, status_code=status)
    await response(scope, receive, send)


class GatewayASGIApp:
    """挂在 /api/mcp-gateway 下的分发器：/{name}/{sse|messages|stream}。"""

    def __init__(self, registry: GatewayRegistry, loader: ConfigLoader) -> None:
        self._registry = registry
        self._loader = loader

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await send_json(scope, receive, send, 404, {"detail": "Not Found"})
            return
        # 挂载在 /api/mcp-gateway 下时，scope["path"] 仍是完整路径，
        # 需要去掉 root_path（挂载前缀）后再解析 /{name}/{kind}
        root = scope.get("root_path") or ""
        raw_path = scope.get("path") or ""
        if root and raw_path.startswith(root):
            raw_path = raw_path[len(root):]
        parts = [p for p in raw_path.split("/") if p]
        if len(parts) != 2:
            await send_json(scope, receive, send, 404, {"detail": "网关端点不存在"})
            return
        name, kind = parts
        config = await self._loader(name)
        if config is None or not config.get("enabled", True):
            await send_json(scope, receive, send, 404, {"detail": f"网关服务 {name} 不存在或已停用"})
            return
        try:
            identity = await authenticate_request(config, scope)
        except GatewayAuthError:
            await send_json(
                scope, receive, send, 401, {"detail": "网关令牌无效（X-Gateway-Token 或 Bearer）"}
            )
            return
        from app.config import get_settings

        allowed, retry_after = check_rate_limit(
            rate_limit_key(identity, config, scope),
            get_settings().mcp_gateway_rate_limit_per_min,
        )
        if not allowed:
            response = JSONResponse(
                {"detail": "请求过于频繁，请稍后重试"},
                status_code=429,
                headers={"Retry-After": str(retry_after)},
            )
            await response(scope, receive, send)
            return
        ep = self._registry.get(name)
        try:
            if kind == "sse":
                await ep.handle_sse(scope, receive, send, config)
            elif kind in ("messages", "stream"):
                await self._handle_jsonrpc(kind, name, config, identity, scope, receive, send, ep)
            else:
                await send_json(scope, receive, send, 404, {"detail": "网关端点不存在"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("网关服务 %s 处理失败", name)
            try:
                await send_json(scope, receive, send, 502, {"detail": str(exc)[:300]})
            except Exception:  # noqa: BLE001
                pass

    async def _handle_jsonrpc(self, kind, name, config, identity, scope, receive, send, ep) -> None:
        """带审计/超时的 JSON-RPC 转发：/stream 的 POST 与 SSE 的 /messages。"""
        from app.config import get_settings

        body, replay = await _read_body(receive)
        calls = parse_mcp_calls(body)
        if not calls:
            if kind == "stream":
                await ep.handle_stream(scope, replay, send)
            else:
                await ep.handle_messages(scope, replay, send)
            return
        conversation_id = _header(scope, "x-conversation-id")[:64]
        capture = _ResponseCapture(send)
        started = time.perf_counter()
        timeout = float(get_settings().mcp_gateway_request_timeout or 0)
        ok, error = True, ""
        try:
            if kind == "stream":
                if timeout > 0:
                    await asyncio.wait_for(ep.handle_stream(scope, replay, capture), timeout=timeout)
                else:
                    await ep.handle_stream(scope, replay, capture)
            else:
                await ep.handle_messages(scope, replay, capture)
        except asyncio.TimeoutError:
            ok = False
            error = f"网关请求超时（{timeout:g}s）"
            if not capture.started:
                await send_json(
                    scope,
                    receive,
                    send,
                    200,
                    {"jsonrpc": "2.0", "id": calls[0].rpc_id, "error": {"code": -32001, "message": error}},
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            ok = False
            error = str(exc)
            raise
        finally:
            duration_ms = int((time.perf_counter() - started) * 1000)
            if ok:
                error = extract_rpc_error(capture.status, bytes(capture.body))
                ok = not error
            await record_gateway_calls(
                name, config, identity, calls, conversation_id, duration_ms, ok, error
            )
