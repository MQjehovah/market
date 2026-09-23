"""MCP HTTP 中转网关。

两个方向：
1) 出站：统一的 MCP 客户端连接器（stdio / streamable_http / sse），
   供 Agent 运行时（MCPBridge）与 MCP 能力包使用 —— 平台作为 MCP 客户端，
   通过 HTTP 连接任意语言的远程 MCP server，不再要求容器内安装对应运行时。
2) 入站：把注册的 MCP 服务（stdio 子进程或远程 HTTP/SSE）统一暴露为 HTTP 端点
   （SSE + Streamable HTTP），外部 MCP 客户端（Dify、Claude Desktop、其他 Agent）
   可以像连接普通 MCP server 一样调用，stdio 服务自动转成 HTTP（mcp-proxy 模式）。

端点约定（挂在 /api/mcp-gateway 下）：
    GET  /{name}/sse        SSE 传输的 MCP 能力（GET 建立连接）——能力级主入口
    POST /{name}/messages   SSE 客户端回传 JSON-RPC 消息
    GET/POST/DELETE /{name}/stream   Streamable HTTP 传输的 MCP 能力
    GET/POST/DELETE /relay/{name}/{sse|messages|stream}  管理员登记的 MCP 服务（服务级）
    /cap/{name}/{kind}      能力级过渡别名，行为与裸路径一致
    能力级入口（裸路径与 /cap 别名）Bearer = 市场/SSO/服务令牌，name 支持 name@version 钉版本；
    服务级 /relay 用登记令牌（X-Gateway-Token/Bearer 精确匹配）。
    鉴权后限流/熔断；调用写 UsageEvent（含 conversation_id/耗时）
"""

import asyncio
import hmac
import io
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
import zipfile
from contextlib import asynccontextmanager
from dataclasses import dataclass
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
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

logger = logging.getLogger("market.mcp_gateway")

ENV_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
CONNECT_TIMEOUT = 30
CALL_TIMEOUT = 120
_GENERIC_PYTHON = {"python", "python3", "py"}

ConfigLoader = Callable[[str], Awaitable[dict[str, Any] | None]]


def _conversation_id_from_scope(scope) -> str:
    request = Request(scope, None)  # type: ignore[arg-type]
    for key in ("x-conversation-id", "x-session-id"):
        val = (request.headers.get(key) or "").strip()
        if val:
            return val[:128]
    qs = scope.get("query_string") or b""
    if b"conversation_id=" in qs:
        from urllib.parse import parse_qs

        params = parse_qs(qs.decode("utf-8", errors="ignore"))
        vals = params.get("conversation_id") or []
        if vals and vals[0].strip():
            return vals[0].strip()[:128]
    return ""


async def persist_gateway_usage(
    *,
    user_id: str,
    capability_id: str,
    capability_version: str,
    action: str,
    params: dict[str, Any],
    result_status: str,
    duration_ms: int,
    conversation_id: str,
) -> None:
    """网关路径异步落审计（独立会话，避免占用请求会话）。"""
    from sqlalchemy import update

    from app.database import SessionLocal
    from app.models import Capability, UsageEvent

    try:
        async with SessionLocal() as db:
            cap = await db.get(Capability, capability_id)
            if cap is None:
                return
            await db.execute(
                update(Capability)
                .where(Capability.id == capability_id)
                .values(usage_count=Capability.usage_count + 1)
            )
            db.add(
                UsageEvent(
                    user_id=user_id,
                    capability_id=capability_id,
                    capability_version=capability_version or cap.version or "",
                    action=action,
                    params=params,
                    result_status=result_status,
                    duration_ms=max(0, int(duration_ms or 0)),
                    conversation_id=(conversation_id or "")[:128],
                    source="platform",
                )
            )
            await db.commit()
    except Exception:  # noqa: BLE001
        logger.exception("网关审计写入失败 action=%s cap=%s", action, capability_id)


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


def resolve_placeholders(value: Any, extra_env: dict[str, str] | None = None) -> Any:
    """递归解析 ${VAR} / ${VAR:default} 占位符（env/headers/args 中的敏感信息）。

    extra_env：用户托管密钥等覆盖层，优先于进程环境变量。
    """
    overlay = extra_env or {}

    def _sub(m: re.Match) -> str:
        full = m.group(1).strip()
        var, _, default = full.partition(":")
        real = overlay.get(var) or os.environ.get(var, "")
        if not real and default:
            real = default
        return real

    if isinstance(value, dict):
        return {k: resolve_placeholders(v, extra_env) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(v, extra_env) for v in value]
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
    if package_files is None:
        raw_files = config.get("package_files")
        package_files = raw_files if isinstance(raw_files, dict) else None

    user_env = config.get("_user_env") if isinstance(config.get("_user_env"), dict) else {}
    try:
        if transport == "stdio":
            command = config.get("command") or "python"
            # 能力包里常见 "python"；用当前解释器，保证与平台同环境依赖一致
            if str(command).strip().lower() in _GENERIC_PYTHON:
                command = sys.executable
            args = list(config.get("args") or [])
            env = resolve_placeholders(config.get("env") or {}, user_env)
            cwd: str | None = config.get("cwd") or None
            if package_files:
                tmpdir = Path(tempfile.mkdtemp(prefix=f"mcp-gw-{sanitize(config['name'])}-"))
                cleanup.append(tmpdir)
                if extract_implementation(tmpdir, package_files):
                    cwd = str(tmpdir)
                    args = [rewrite_stdio_script_arg(tmpdir, a) for a in args]
            merged_env = dict(os.environ)
            merged_env["PYTHONUNBUFFERED"] = "1"
            if user_env:
                merged_env.update({str(k): str(v) for k, v in user_env.items()})
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
            headers = resolve_placeholders(config.get("headers") or {}, user_env)
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
            headers = resolve_placeholders(config.get("headers") or {}, user_env)
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


def _bearer_token(scope) -> str | None:
    request = Request(scope, None)  # type: ignore[arg-type]
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
        return token or None
    return None


async def find_published_mcp(db, name: str, version: str | None = None):
    """已发布/弃用期内的 MCP 能力（含制品）；version 为空取最新。"""
    from sqlalchemy import select
    from sqlalchemy.orm import joinedload, selectinload

    from app.models import Capability
    from app.services.capabilities import parse_semver, split_cap_ref

    name, ref_ver = split_cap_ref(name)
    version = version or ref_ver
    stmt = (
        select(Capability)
        .options(selectinload(Capability.artifacts), joinedload(Capability.author))
        .where(
            Capability.name == name,
            Capability.type == "mcp",
            Capability.status.in_(("published", "deprecated")),
        )
    )
    if version:
        stmt = stmt.where(Capability.version == version)
    rows = list((await db.scalars(stmt)).all())
    if not rows:
        return None
    if version:
        return rows[0]
    return max(rows, key=lambda c: parse_semver(c.version))


async def upstream_config_from_capability(db, cap) -> dict[str, Any] | None:
    """已发布 MCP → connect_upstream 配置（stdio 带 package_files；gateway 解析登记表）。"""
    files = read_package_files(cap)
    raw = files.get("connection.json")
    if not raw:
        return None
    try:
        conn = json.loads(raw.decode("utf-8-sig"))
    except (ValueError, UnicodeDecodeError):
        raise RuntimeError("connection.json 解析失败") from None
    if not isinstance(conn, dict):
        raise RuntimeError("connection.json 顶层必须是对象")
    transport = str(conn.get("transport") or conn.get("type") or "stdio").strip().lower()
    if transport == "gateway":
        cfg = await load_gateway_config_by_name(db, conn.get("server") or "")
        if cfg is None:
            raise RuntimeError(f"网关服务 {conn.get('server') or ''} 不存在")
        cfg = dict(cfg)
        cfg["enabled"] = True
        return cfg
    if transport not in ("http", "streamable_http", "sse", "stdio"):
        raise RuntimeError(f"不支持 transport={transport}")
    return {
        "name": cap.name,
        "transport": "streamable_http" if transport == "http" else transport,
        "url": conn.get("url") or "",
        "headers": conn.get("headers") or {},
        "command": conn.get("command") or "python",
        "args": list(conn.get("args") or []),
        "env": conn.get("env") or {},
        "cwd": conn.get("cwd") or "",
        "enabled": True,
        "package_files": files,
    }


async def authorize_capability_gateway(
    scope, name: str
) -> tuple[dict[str, Any] | None, int | None, dict | None]:
    """能力级网关鉴权：Bearer（HS256|SSO|服务令牌）+ runtime 门禁 + 限流/熔断。

    返回 (config, err_status, err_body)。config 含 `_audit` 元数据供调用审计。
    """
    from fastapi import HTTPException

    from app.auth import get_current_user
    from app.database import SessionLocal
    from app.permissions import require_runtime_access
    from app.services.capabilities import split_cap_ref
    from app.services.gateway_governance import check_circuit, check_rate_limit

    token = _bearer_token(scope)
    if not token:
        return None, 401, {"detail": "请先完成企业 SSO 登录（访问能力 MCP 网关需要 Bearer）"}

    cap_name, _ = split_cap_ref(name)
    circuit_key = f"mcp:{cap_name}"
    ok, reason = check_circuit(circuit_key)
    if not ok:
        return None, 503, {"detail": reason}

    async with SessionLocal() as db:
        try:
            user = await get_current_user(token, db)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            return None, int(exc.status_code), {"detail": detail}

        ok, reason = check_rate_limit(f"user:{user.id}")
        if not ok:
            return None, 429, {"detail": reason}

        cap = await find_published_mcp(db, name)
        if cap is None:
            return None, 404, {"detail": f"已发布 MCP {name} 不存在"}
        dist = getattr(cap, "distribution", None) or "both"
        if dist == "local":
            return None, 403, {"detail": f"能力 {cap.name} 仅支持本地分发（distribution=local）"}
        try:
            await require_runtime_access(user, cap, db)
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
            return None, int(exc.status_code), {"detail": detail}
        try:
            config = await upstream_config_from_capability(db, cap)
        except RuntimeError as exc:
            return None, 502, {"detail": str(exc)[:300]}
        if config is None:
            return None, 404, {"detail": f"MCP {name} 缺少 connection.json"}
        config = dict(config)
        from app.services.secret_vault import attach_user_env, resolve_user_env

        user_env = await resolve_user_env(db, user.id, capability_id=cap.id)
        config = attach_user_env(config, user_env)
        config["_audit"] = {
            "user_id": user.id,
            "capability_id": cap.id,
            "capability_version": cap.version,
            "capability_name": cap.name,
            "conversation_id": _conversation_id_from_scope(scope),
            "circuit_key": circuit_key,
        }
        return config, None, None


def make_forward_server(name: str, upstream: ClientSession, audit: dict[str, Any] | None = None) -> MCPServer:
    """构建转发型低层 MCP server：tools/list 与 tools/call 全部转发到上游。"""

    server = MCPServer(f"gateway-{name}")
    audit = audit or {}

    @server.list_tools()
    async def list_tools():
        result = await upstream.list_tools()
        return result.tools

    @server.call_tool()
    async def call_tool(tool_name: str, arguments: dict[str, Any]):
        from app.services.gateway_governance import record_failure, record_success

        t0 = time.monotonic()
        result_status = "ok"
        try:
            result = await upstream.call_tool(tool_name, arguments or {})
            if audit.get("circuit_key"):
                record_success(audit["circuit_key"])
            return result
        except Exception:
            result_status = "error"
            if audit.get("circuit_key"):
                record_failure(audit["circuit_key"])
            raise
        finally:
            if audit.get("user_id") and audit.get("capability_id"):
                await persist_gateway_usage(
                    user_id=audit["user_id"],
                    capability_id=audit["capability_id"],
                    capability_version=audit.get("capability_version") or "",
                    action="gateway_call",
                    params={
                        "tool": tool_name,
                        "capability": audit.get("capability_name") or name,
                        "version": audit.get("capability_version") or "",
                    },
                    result_status=result_status,
                    duration_ms=int((time.monotonic() - t0) * 1000),
                    conversation_id=audit.get("conversation_id") or "",
                )

    return server

class _ServerProxy:
    """StreamableHTTPSessionManager 使用的“按会话建上游”的 server 代理。

    manager 每个客户端会话会调用一次 run()，我们在该会话内连接独立的上游，
    保证 stdio 子进程 / HTTP 会话的生命周期与客户端会话一致。
    配置优先用 GatewayEndpoints 在鉴权后缓存的 config（能力级 /{name}，别名 /cap/{name}），
    否则回退登记表 loader。
    """

    def __init__(self, endpoints: "GatewayEndpoints") -> None:
        self._ep = endpoints

    def create_initialization_options(self, **kwargs) -> InitializationOptions:
        return InitializationOptions(
            server_name=f"gateway-{self._ep.name}",
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
        config = await self._ep.resolve_config()
        if config is None or not config.get("enabled", True):
            raise RuntimeError(f"网关服务 {self._ep.name} 不存在或已停用")
        async with connect_upstream(config) as upstream:
            server = make_forward_server(
                self._ep.name, upstream, audit=config.get("_audit")
            )
            await server.run(
                read_stream,
                write_stream,
                initialization_options,
                raise_exceptions=raise_exceptions,
                stateless=stateless,
            )


class GatewayEndpoints:
    """单个网关端点的入站实现：SSE + Streamable HTTP + 消息接收。

    name：登记服务名，或能力级的 ``cap/{能力名}`` 路由键；同时作为 loader 回退键与审计名。
    route_path：SSE 消息端点相对路径（服务级 ``relay/{name}``；能力级默认与 name 相同）。
    """

    def __init__(self, name: str, loader: ConfigLoader, route_path: str | None = None) -> None:
        self.name = name
        self._loader = loader
        self._cached_config: dict[str, Any] | None = None
        self.sse = SseServerTransport(endpoint=f"/{(route_path or name).strip('/')}/messages/")
        self._stream_manager: StreamableHTTPSessionManager | None = None
        self._stream_task: asyncio.Task | None = None
        self._stream_ready = asyncio.Event()

    def cache_config(self, config: dict[str, Any] | None) -> None:
        self._cached_config = config

    async def resolve_config(self) -> dict[str, Any] | None:
        if self._cached_config is not None:
            return self._cached_config
        return await self._loader(self.name)

    async def ensure_stream_manager(self) -> None:
        if self._stream_task is None:
            self._stream_task = asyncio.create_task(self._run_stream_manager())
        await self._stream_ready.wait()

    async def _run_stream_manager(self) -> None:
        manager = StreamableHTTPSessionManager(
            _ServerProxy(self), json_response=True
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
        self.cache_config(config)
        audit = config.get("_audit") or {}
        if audit.get("user_id") and audit.get("capability_id"):
            await persist_gateway_usage(
                user_id=audit["user_id"],
                capability_id=audit["capability_id"],
                capability_version=audit.get("capability_version") or "",
                action="mcp_connect",
                params={
                    "transport": "sse",
                    "capability": audit.get("capability_name") or self.name,
                    "version": audit.get("capability_version") or "",
                },
                result_status="ok",
                duration_ms=0,
                conversation_id=audit.get("conversation_id") or "",
            )
        async with self.sse.connect_sse(scope, receive, send) as streams:
            async with connect_upstream(config) as upstream:
                server = make_forward_server(
                    self.name, upstream, audit=config.get("_audit")
                )
                await server.run(
                    streams[0], streams[1], server.create_initialization_options()
                )

    async def handle_messages(self, scope, receive, send) -> None:
        await self.sse.handle_post_message(scope, receive, send)

    async def handle_stream(self, scope, receive, send, config: dict[str, Any] | None = None) -> None:
        if config is not None:
            self.cache_config(config)
            audit = config.get("_audit") or {}
            if audit.get("user_id") and audit.get("capability_id"):
                await persist_gateway_usage(
                    user_id=audit["user_id"],
                    capability_id=audit["capability_id"],
                    capability_version=audit.get("capability_version") or "",
                    action="mcp_connect",
                    params={
                        "transport": "stream",
                        "capability": audit.get("capability_name") or self.name,
                        "version": audit.get("capability_version") or "",
                    },
                    result_status="ok",
                    duration_ms=0,
                    conversation_id=audit.get("conversation_id") or "",
                )
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
    """按路由键持有入站端点实例（能力级 ``cap/{name}`` / 服务级登记名）。"""

    def __init__(self, loader: ConfigLoader) -> None:
        self._loader = loader
        self._endpoints: dict[str, GatewayEndpoints] = {}

    def get(self, name: str, route_path: str | None = None) -> GatewayEndpoints:
        ep = self._endpoints.get(name)
        if ep is None:
            ep = GatewayEndpoints(name, self._loader, route_path)
            self._endpoints[name] = ep
        return ep

    async def shutdown(self) -> None:
        for ep in self._endpoints.values():
            await ep.shutdown()
        self._endpoints.clear()


@dataclass
class GatewayIdentity:
    """入站请求身份来源：server_token | jwt | sso | service_token | anonymous。

    仅用于审计归因；鉴权动作仍由 authorize_capability_gateway（能力级 /{name} 与
    /cap/{name} 别名）与 check_token（服务级 /relay/{name}）执行，匿名仅服务级路由在未配置令牌时可能出现。
    """

    source: str
    user_id: str | None = None
    username: str = ""
    role: str = ""
    user: Any = None  # ORM User；仅 jwt/sso/service_token 来源非空


class GatewayAuthError(Exception):
    """网关鉴权失败（对应 401）。"""


def _header(scope, name: str) -> str:
    """从 ASGI scope 读首个同名 header（name 需小写）。"""
    for key, value in scope.get("headers") or []:
        if key.decode("latin-1").lower() == name:
            return value.decode("latin-1")
    return ""


async def _resolve_bearer_identity(token: str) -> GatewayIdentity | None:
    """Bearer → 本地 JWT / 服务令牌 / SSO 身份；均不匹配返回 None（短会话，无依赖注入）。"""
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
    """识别入站请求身份来源（供审计）：per-server token → Bearer → 匿名。

    1) api_token 非空时，X-Gateway-Token / Bearer 精确匹配 → server_token；
    2) Bearer 依次尝试本地 JWT / 服务令牌 / SSO；
    3) 都不匹配：api_token 非空或 require_token=True 抛 GatewayAuthError，
       否则匿名（仅服务级路由可能出现）。
    """
    from app.config import get_settings

    expected = config.get("api_token") or ""
    if expected:
        candidates = (_header(scope, "x-gateway-token"), _bearer_token(scope) or "")
        if any(c and hmac.compare_digest(c, expected) for c in candidates):
            return GatewayIdentity(source="server_token")
    bearer = _bearer_token(scope) or ""
    if bearer:
        identity = await _resolve_bearer_identity(bearer)
        if identity is not None:
            return identity
    if expected or get_settings().mcp_gateway_require_token:
        raise GatewayAuthError("网关令牌无效")
    return GatewayIdentity(source="anonymous")


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
    """消息级审计：每条 JSON-RPC 消息写一行 MCPGatewayCall；写失败仅告警不阻断。

    不做用量计数（UsageEvent/usage_count 由 persist_gateway_usage 负责，避免双记账）。
    """
    if not calls:
        return
    from app.database import SessionLocal
    from app.models import Capability, MCPGatewayCall

    error = (error or "")[:300]
    try:
        async with SessionLocal() as db:
            audit = config.get("_audit") or {}
            cap_id = config.get("capability_id") or audit.get("capability_id") or ""
            cap = None
            if cap_id:
                cap = await db.get(Capability, cap_id)
            cap_version = audit.get("capability_version") or (
                cap.version if cap is not None else ""
            )
            for call in calls:
                db.add(
                    MCPGatewayCall(
                        server_name=server_name,
                        capability_id=cap_id,
                        capability_version=cap_version,
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


async def check_token(config: dict[str, Any], scope) -> bool:
    """网关端点鉴权：X-Gateway-Token 或 Authorization: Bearer。

    留空令牌：默认内部免鉴权；若 settings.mcp_gateway_require_token=True 则拒绝。
    """
    from app.config import get_settings

    expected = config.get("api_token") or ""
    if not expected:
        return not get_settings().mcp_gateway_require_token
    request = Request(scope, None)  # type: ignore[arg-type]
    provided = request.headers.get("x-gateway-token", "")
    if not provided:
        auth = request.headers.get("authorization", "")
        if auth.lower().startswith("bearer "):
            provided = auth[7:].strip()
    return bool(provided) and hmac.compare_digest(provided, expected)


async def send_json(scope, receive, send, status: int, body: dict) -> None:
    response = JSONResponse(body, status_code=status)
    await response(scope, receive, send)


class GatewayASGIApp:
    """挂在 /api/mcp-gateway 下的分发器。

    路由判定（保留 3 段先于 2 段的顺序）：
    - 3 段 ``relay/{name}/{kind}`` → 服务级（登记表 + check_token）
    - 3 段 ``cap/{name}/{kind}``   → 能力级过渡别名
    - 2 段 ``{name}/{kind}``       → 能力级主入口（``relay`` / ``cap`` 为保留字，拒绝）
    """

    def __init__(self, registry: GatewayRegistry, loader: ConfigLoader) -> None:
        self._registry = registry
        self._loader = loader

    async def __call__(self, scope, receive, send) -> None:
        if scope["type"] != "http":
            await send_json(scope, receive, send, 404, {"detail": "Not Found"})
            return
        # 挂载在 /api/mcp-gateway 下时，scope["path"] 仍是完整路径，
        # 需要去掉 root_path（挂载前缀）后再解析 /{name}/{kind}、/cap/{name}/{kind} 或 /relay/{name}/{kind}
        root = scope.get("root_path") or ""
        raw_path = scope.get("path") or ""
        if root and raw_path.startswith(root):
            raw_path = raw_path[len(root):]
        from urllib.parse import unquote

        parts = [unquote(p) for p in raw_path.split("/") if p]
        route_path: str | None = None
        if len(parts) == 3 and parts[0] == "relay":
            # 服务级：原裸路由改名到 /relay，注册名与审计名保持为 name
            name, kind = parts[1], parts[2]
            registry_key = name
            route_path = f"relay/{name}"
            config = await self._loader(name)
            if config is None or not config.get("enabled", True):
                await send_json(scope, receive, send, 404, {"detail": f"网关服务 {name} 不存在或已停用"})
                return
            if not await check_token(config, scope):
                await send_json(
                    scope, receive, send, 401, {"detail": "网关令牌无效（X-Gateway-Token 或 Bearer）"}
                )
                return
        elif (len(parts) == 3 and parts[0] == "cap") or len(parts) == 2:
            # 能力级：裸路径为主入口，/cap 为过渡别名
            if len(parts) == 3:
                name, kind = parts[1], parts[2]
            else:
                name, kind = parts
                if name in ("relay", "cap"):
                    await send_json(
                        scope,
                        receive,
                        send,
                        404,
                        {
                            "detail": (
                                f"网关端点不存在：{name} 为网关保留字，"
                                "能力级请使用 /{能力名}/{kind}"
                            )
                        },
                    )
                    return
            registry_key = f"cap/{name}"
            config, err_status, err_body = await authorize_capability_gateway(scope, name)
            if err_status is not None:
                await send_json(scope, receive, send, err_status, err_body or {"detail": "未授权"})
                return
            if config is None:
                await send_json(scope, receive, send, 502, {"detail": "无法构建上游配置"})
                return
        else:
            await send_json(scope, receive, send, 404, {"detail": "网关端点不存在"})
            return
        ep = self._registry.get(registry_key, route_path=route_path)
        identity = await self._audit_identity(config, scope)
        try:
            if kind == "sse":
                await ep.handle_sse(scope, receive, send, config)
            elif kind == "messages":
                await self._handle_jsonrpc(kind, registry_key, config, identity, scope, receive, send, ep)
            elif kind == "stream":
                await self._handle_jsonrpc(kind, registry_key, config, identity, scope, receive, send, ep)
            else:
                await send_json(scope, receive, send, 404, {"detail": "网关端点不存在"})
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            logger.exception("网关服务 %s 处理失败", registry_key)
            try:
                await send_json(scope, receive, send, 502, {"detail": str(exc)[:300]})
            except Exception:  # noqa: BLE001
                pass

    async def _audit_identity(self, config: dict[str, Any], scope) -> GatewayIdentity:
        """身份来源仅用于审计归因；鉴权已在前一步完成，失败回退匿名。"""
        try:
            identity = await authenticate_request(config, scope)
        except GatewayAuthError:
            identity = GatewayIdentity(source="anonymous")
        audit = config.get("_audit") or {}
        if identity.user_id is None and audit.get("user_id"):
            identity.user_id = audit["user_id"]
        return identity

    async def _handle_jsonrpc(
        self, kind, name, config, identity, scope, receive, send, ep
    ) -> None:
        """带消息级审计与 /stream 单请求超时的 JSON-RPC 转发（/messages 不设总超时）。"""
        from app.config import get_settings

        body, replay = await _read_body(receive)
        calls = parse_mcp_calls(body)
        if not calls:
            if kind == "stream":
                await ep.handle_stream(scope, replay, send, config)
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
                    await asyncio.wait_for(
                        ep.handle_stream(scope, replay, capture, config), timeout=timeout
                    )
                else:
                    await ep.handle_stream(scope, replay, capture, config)
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
