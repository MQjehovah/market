"""能力包校验：根目录文件、整夹压缩、缺文件 422。"""

import io
import json
import zipfile

import pytest
from fastapi import HTTPException

from app.services.packages import prepare_package, validate_package


def _zip_bytes(files: dict[str, bytes | str]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name, data in files.items():
            zf.writestr(name, data if isinstance(data, bytes) else data.encode("utf-8"))
    return buf.getvalue()


def _tool_files(*, prefix: str = "", bom: bool = False) -> dict[str, bytes]:
    meta = json.dumps({"name": "文件哈希计算", "version": "1.0.0"}, ensure_ascii=False)
    schema = json.dumps({"type": "object", "properties": {"path": {"type": "string"}}})
    if bom:
        meta_bytes = b"\xef\xbb\xbf" + meta.encode("utf-8")
    else:
        meta_bytes = meta.encode("utf-8")
    p = f"{prefix}/" if prefix else ""
    return {
        f"{p}tool.json": meta_bytes,
        f"{p}schema.json": schema.encode("utf-8"),
        f"{p}implementation/tool.py": b"def run(params):\n    return {'ok': True}\n",
    }


def test_validate_tool_package_at_zip_root():
    details = validate_package("tool", _zip_bytes(_tool_files()))
    assert details["meta"]["name"] == "文件哈希计算"
    assert "path" in details["schema"]["properties"]


def test_validate_accepts_wrapped_folder_and_macos_junk():
    files = _tool_files(prefix="my-tool")
    files["__MACOSX/my-tool/._tool.json"] = b"junk"
    files["my-tool/.DS_Store"] = b"junk"
    details = validate_package("tool", _zip_bytes(files))
    assert details["meta"]["name"] == "文件哈希计算"
    assert "tool.json" in details["files"]


def test_prepare_flattens_wrapped_folder():
    content, details = prepare_package("tool", _zip_bytes(_tool_files(prefix="my-tool")))
    assert details["meta"]["name"] == "文件哈希计算"
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        names = set(zf.namelist())
    assert "tool.json" in names
    assert "implementation/tool.py" in names
    assert not any(n.startswith("my-tool/") for n in names)


def test_validate_utf8_bom_json():
    details = validate_package("tool", _zip_bytes(_tool_files(bom=True)))
    assert details["meta"]["name"] == "文件哈希计算"


def test_missing_required_file_is_422():
    raw = _zip_bytes({"tool.json": '{"name": "x", "version": "1.0.0"}'})
    with pytest.raises(HTTPException) as exc:
        validate_package("tool", raw)
    assert exc.value.status_code == 422
    assert "schema.json" in str(exc.value.detail)


@pytest.mark.asyncio
async def test_upload_wrapped_tool_zip(client, publisher_headers):
    r = await client.post(
        "/api/publish/capabilities",
        headers=publisher_headers,
        json={"name": "整夹压缩工具", "description": "test", "type": "tool", "version": "0.1.0"},
    )
    assert r.status_code == 201, r.text
    cap_id = r.json()["id"]
    r = await client.post(
        f"/api/publish/capabilities/{cap_id}/artifact",
        headers=publisher_headers,
        files={"file": ("tool.zip", _zip_bytes(_tool_files(prefix="hash-tool")), "application/zip")},
    )
    assert r.status_code == 200, r.text
    assert r.json()["artifacts"]
    assert r.json()["input_schema"]["properties"]["path"]["type"] == "string"


# ---- 标准优先: MCP server.json / Skill SKILL.md ----

def test_validate_mcp_server_json_standard():
    server = {
        "name": "io.github.xzrobot/weather",
        "version": "1.2.0",
        "description": "weather server",
        "remotes": [{"type": "streamable-http", "url": "https://mcp.example.com/mcp"}],
    }
    details = validate_package("mcp", _zip_bytes({"server.json": json.dumps(server)}))
    assert details["meta"]["slug"] == "io.github.xzrobot/weather"
    assert details["meta"]["name"] == "io.github.xzrobot/weather"
    assert details["connection"]["url"] == "https://mcp.example.com/mcp"
    assert details["connection"]["transport"] == "streamable-http"


def test_validate_mcp_server_json_bad_name_422():
    server = {"name": "Bad Name", "version": "1.0.0"}
    with pytest.raises(HTTPException) as exc:
        validate_package("mcp", _zip_bytes({"server.json": json.dumps(server)}))
    assert exc.value.status_code == 422


def test_validate_mcp_legacy_four_files_still_supported():
    files = {
        "mcp.json": json.dumps({"name": "旧连接器", "version": "1.0.0"}, ensure_ascii=False),
        "connection.json": json.dumps({"transport": "stdio", "command": "python", "args": ["s.py"]}),
        "tools.json": json.dumps({"tools": []}),
        "security.json": json.dumps({"sandbox": False}),
    }
    details = validate_package("mcp", _zip_bytes(files))
    assert details["meta"]["name"] == "旧连接器"


def test_validate_skill_md_only_standard():
    md = (
        "---\nname: pdf-processing\ndescription: Extract PDF text. Use when handling PDFs.\n---\n\n"
        "Body instructions here.\n"
    )
    details = validate_package("skill", _zip_bytes({"SKILL.md": md}))
    assert details["meta"]["name"] == "pdf-processing"
    assert details["meta"]["slug"] == "pdf-processing"


def test_validate_skill_md_bad_standard_name_422():
    md = "---\nname: PDF-Processing\ndescription: x\n---\n"
    with pytest.raises(HTTPException) as exc:
        validate_package("skill", _zip_bytes({"SKILL.md": md}))
    assert exc.value.status_code == 422


def test_registry_import_mapping_only_metadata():
    from app.services.registry_import import to_capability_fields

    server = {
        "name": "ac.inference.sh/mcp",
        "title": "inference.sh",
        "version": "1.0.1",
        "description": "x",
        "remotes": [{"type": "streamable-http", "url": "https://sh.inference.ac"}],
        "_meta": {"io.modelcontextprotocol.registry/official": {"status": "active"}},
    }
    fields = to_capability_fields(server)
    assert fields["slug"] == "ac.inference.sh/mcp"
    assert fields["name"] == "ac.inference.sh_mcp"  # '/' → '_' 的内部名
    assert fields["display_name"] == "inference.sh"
    assert fields["type"] == "mcp"
    assert fields["distribution"] == "remote"
    assert fields["visibility"] == "private"
    assert fields["binding"] == "service"
    assert fields["provenance"]["origin"] == "mcp-registry"
    assert fields["provenance"]["server_json"]["name"] == "ac.inference.sh/mcp"
