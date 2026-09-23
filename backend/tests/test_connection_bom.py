"""connection.json 带 UTF-8 BOM 时也能被网关解析（Windows 工具链常见，曾致 /cap 502）。"""

import io
import json
import zipfile
from types import SimpleNamespace

import pytest

from app.services import mcp_gateway


class _FakeStorage:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def open(self, uri: str) -> io.BytesIO:  # noqa: ARG002
        return io.BytesIO(self._data)


def _mcp_zip(connection_bytes: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("connection.json", connection_bytes)
        zf.writestr("mcp.json", json.dumps({"name": "time", "description": "x", "version": "1.0.0"}))
        zf.writestr("tools.json", json.dumps({"tools": []}))
        zf.writestr("security.json", json.dumps({"notes": ""}))
    return buf.getvalue()


@pytest.mark.asyncio
async def test_connection_json_with_bom_is_accepted(monkeypatch):
    conn = {"transport": "stdio", "command": "python", "args": ["implementation/server.py"]}
    payload = ("\ufeff" + json.dumps(conn)).encode("utf-8")
    monkeypatch.setattr("app.storage.get_storage", lambda: _FakeStorage(_mcp_zip(payload)))

    cap = SimpleNamespace(name="time", version="1.0.0", artifacts=[SimpleNamespace(uri="mem://x")])
    cfg = await mcp_gateway.upstream_config_from_capability(None, cap)

    assert cfg is not None
    assert cfg["transport"] == "stdio"
    assert cfg["command"] == "python"
    assert cfg["args"] == ["implementation/server.py"]


@pytest.mark.asyncio
async def test_connection_json_without_bom_still_works(monkeypatch):
    conn = {"transport": "sse", "url": "http://example.com/mcp"}
    payload = json.dumps(conn).encode("utf-8")
    monkeypatch.setattr("app.storage.get_storage", lambda: _FakeStorage(_mcp_zip(payload)))

    cap = SimpleNamespace(name="sse-demo", version="1.0.0", artifacts=[SimpleNamespace(uri="mem://y")])
    cfg = await mcp_gateway.upstream_config_from_capability(None, cap)

    assert cfg is not None
    assert cfg["transport"] == "sse"
    assert cfg["url"] == "http://example.com/mcp"
