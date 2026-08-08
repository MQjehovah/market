"""能力发布包结构与校验（README 3.x 各市场发布包规范）。"""

import io
import json
import zipfile
from typing import Any

from fastapi import HTTPException, status

REQUIRED_FILES: dict[str, list[str]] = {
    "agent": ["agent.json", "PROMPT.md"],
    "tool": ["tool.json", "schema.json", "implementation/tool.py"],
    "skill": ["skill.json", "SKILL.md"],
    "mcp": ["mcp.json", "connection.json", "tools.json", "security.json"],
}

OPTIONAL_FILES: dict[str, list[str]] = {
    "agent": ["TEAM.md", "tools.json", "knowledge/", "skills/", "examples/"],
    "tool": ["security.json", "tests/", "examples/", "docs/", "implementation/__init__.py"],
    "skill": ["templates/", "assets/", "dependencies.json", "examples/"],
    "mcp": ["docker-compose.yml", "docs/"],
}


def _read_json(zf: zipfile.ZipFile, name: str) -> dict[str, Any]:
    try:
        data = json.loads(zf.read(name).decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"文件 {name} 不是合法的 UTF-8 JSON：{exc}",
        )
    if not isinstance(data, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"文件 {name} 顶层必须是 JSON 对象")
    return data


def validate_package(capability_type: str, content: bytes) -> dict[str, Any]:
    """校验 zip 能力包。返回解析出的元数据信息。"""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "上传文件不是有效的 zip 包")

    names = set(zf.namelist())
    for required in REQUIRED_FILES.get(capability_type, []):
        if required not in names:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{capability_type} 包缺少必需文件：{required}",
            )

    meta_file = {
        "agent": "agent.json",
        "tool": "tool.json",
        "skill": "skill.json",
        "mcp": "mcp.json",
    }[capability_type]
    meta = _read_json(zf, meta_file)
    if "name" not in meta:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{meta_file} 缺少 name 字段")

    details: dict[str, Any] = {"meta": meta}
    if capability_type == "mcp":
        conn = _read_json(zf, "connection.json")
        details["connection"] = conn
    elif capability_type == "tool":
        details["schema"] = _read_json(zf, "schema.json")
    return details
