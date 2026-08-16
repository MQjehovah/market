"""云端 MCP 桥接：从能力包连接 MCP 服务器，把其工具暴露给 Agent 执行器。"""

import asyncio
import io
import json
import logging
import re
import shutil
import zipfile
from contextlib import AsyncExitStack
from typing import Any

from app.services.mcp_gateway import connect_upstream, load_gateway_config_by_name
from app.storage import get_storage

logger = logging.getLogger("market.mcp_bridge")

CONNECT_TIMEOUT = 30
CALL_TIMEOUT = 60


def _sanitize(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]", "_", name)[:32] or "mcp"


def _read_package(cap) -> dict[str, bytes]:
    if not getattr(cap, "artifacts", None):
        return {}
    try:
        content = get_storage().open(cap.artifacts[-1].uri).read()
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            return {name: zf.read(name) for name in zf.namelist() if not name.endswith("/")}
    except Exception:  # noqa: BLE001
        return {}


class MCPBridge:
    """一次 Agent 运行期间管理的 MCP 连接池。"""

    def __init__(self, gateway_loader=None) -> None:
        self._stack = AsyncExitStack()
        self._servers: dict[str, dict[str, Any]] = {}
        self._tool_map: dict[str, tuple[str, str]] = {}
        self.tool_defs: list[dict[str, Any]] = []
        self.tool_names: list[str] = []
        self._gateway_loader = gateway_loader

    async def connect_capability(self, mcp_name: str, cap) -> dict[str, Any]:
        """连接一个 MCP 能力包，返回连接结果信息。"""
        files = _read_package(cap)
        raw = files.get("connection.json")
        if raw is None:
            return {"name": mcp_name, "connected": False, "error": "能力包缺少 connection.json"}
        try:
            conn = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            return {"name": mcp_name, "connected": False, "error": "connection.json 解析失败"}
        transport = conn.get("transport", "stdio")
        if transport == "gateway":
            if self._gateway_loader is None:
                return {
                    "name": mcp_name,
                    "connected": False,
                    "error": "网关解析器未注入，无法使用 gateway 传输",
                }
            config = await self._gateway_loader(conn.get("server") or "")
            if config is None:
                return {
                    "name": mcp_name,
                    "connected": False,
                    "error": f"网关服务 {conn.get('server') or ''} 不存在",
                }
        elif transport in ("http", "streamable_http", "sse", "stdio"):
            config = {
                "name": mcp_name,
                "transport": "streamable_http" if transport == "http" else transport,
                "url": conn.get("url", ""),
                "headers": conn.get("headers") or {},
                "command": conn.get("command", "python"),
                "args": list(conn.get("args") or []),
                "env": conn.get("env") or {},
                "cwd": conn.get("cwd", ""),
            }
        else:
            return {
                "name": mcp_name,
                "connected": False,
                "error": f"暂不支持 transport={transport}",
            }
        try:
            async with asyncio.timeout(CONNECT_TIMEOUT):
                session = await self._stack.enter_async_context(
                    connect_upstream(config, files)
                )
            tools = (await session.list_tools()).tools
        except Exception as exc:  # noqa: BLE001
            logger.error(f"MCP [{mcp_name}] 连接失败: {exc}")
            return {"name": mcp_name, "connected": False, "error": str(exc)[:300]}

        prefix = f"mcp_{_sanitize(mcp_name)}"
        # 临时实现目录的清理由 connect_upstream 自身负责
        self._servers[prefix] = {"name": mcp_name, "session": session, "cwd": None}
        for tool in tools:
            exposed = f"{prefix}_{tool.name}"
            self._tool_map[exposed] = (prefix, tool.name)
            self.tool_defs.append(
                {
                    "type": "function",
                    "function": {
                        "name": exposed,
                        "description": f"[MCP {mcp_name}] {tool.description or tool.name}",
                        "parameters": tool.inputSchema,
                    },
                }
            )
            self.tool_names.append(exposed)
        logger.info(f"MCP [{mcp_name}] 已连接，暴露 {len(tools)} 个工具")
        return {"name": mcp_name, "connected": True, "tools": len(tools)}

    def has_tool(self, name: str) -> bool:
        return name in self._tool_map

    async def call(self, name: str, args: dict[str, Any]) -> str:
        entry = self._tool_map.get(name)
        if entry is None:
            return json.dumps({"ok": False, "error": f"未知 MCP 工具 {name}"}, ensure_ascii=False)
        prefix, real_name = entry
        server = self._servers.get(prefix)
        if server is None:
            return json.dumps({"ok": False, "error": "MCP 连接不可用"}, ensure_ascii=False)
        try:
            result = await asyncio.wait_for(
                server["session"].call_tool(real_name, args), timeout=CALL_TIMEOUT
            )
            parts = []
            for item in (result.content if hasattr(result, "content") else []):
                text = getattr(item, "text", None)
                if text is not None:
                    parts.append(str(text))
            return "\n".join(parts) or "执行成功"
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False)

    async def close(self) -> None:
        try:
            await self._stack.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass
        for info in self._servers.values():
            cwd = info.get("cwd")
            if cwd:
                shutil.rmtree(cwd, ignore_errors=True)
