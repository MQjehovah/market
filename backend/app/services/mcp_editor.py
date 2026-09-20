"""MCP 在线编辑：读取 / 保存 connection.json 与 implementation/*.py（保存即新版本草稿）。"""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.models import Capability, CapabilityArtifact, User
from app.schemas import McpEditSave, McpImplementationFile
from app.services.capabilities import (
    draft_policy_kwargs,
    merge_edit_package_files,
    next_version,
    package_base_for_save,
    parse_semver,
    read_capability_files,
)

from app.services.packages import validate_mcp_connection
from app.storage import get_storage

_CORE = {"mcp.json", "connection.json", "tools.json"}
_CORE_TEXT = frozenset({"connection.json", "implementation/*.py"})
_IMPL_RE = re.compile(r"^implementation/[\w.\-]+(?:/[\w.\-]+)*\.py$")
_DEFAULT_SERVER = "implementation/server.py"
_DEFAULT_SERVER_STUB = (
    '"""MCP stdio server 入口。网关会把 implementation/*.py 解到临时目录后按 connection.args 启动。"""\n'
    "\n"
    "\n"
    "def main() -> None:\n"
    '    raise SystemExit("请实现 MCP server")\n'
    "\n"
    "\n"
    'if __name__ == "__main__":\n'
    "    main()\n"
)


def _read_zip(cap: Capability) -> dict[str, bytes] | None:
    files = read_capability_files(cap)
    return files or None


def _loads(raw: bytes | None, default: Any = None) -> Any:
    if not raw:
        return default
    try:
        return json.loads(raw.decode("utf-8-sig"))
    except Exception:  # noqa: BLE001
        return default


def _is_impl(path: str) -> bool:
    return path.startswith("implementation/") and path.endswith(".py")


def _file_list(files: dict[str, bytes]) -> list[dict[str, Any]]:
    return [
        {"path": name, "size": len(data)}
        for name, data in files.items()
        if name not in _CORE and not _is_impl(name)
    ]


def _file_list_from_zip(pkg: bytes) -> list[dict[str, Any]]:
    with zipfile.ZipFile(io.BytesIO(pkg)) as zf:
        return [
            {"path": name, "size": zf.getinfo(name).file_size}
            for name in zf.namelist()
            if not name.endswith("/") and name not in _CORE and not _is_impl(name)
        ]


def _read_implementations(files: dict[str, bytes]) -> list[McpImplementationFile]:
    rows = [
        McpImplementationFile(
            path=name,
            content=data.decode("utf-8", errors="replace"),
        )
        for name, data in files.items()
        if _is_impl(name)
    ]
    rows.sort(key=lambda r: (0 if r.path == _DEFAULT_SERVER else 1, r.path))
    return rows


def _normalize_implementations(
    items: list[McpImplementationFile] | None,
) -> list[McpImplementationFile] | None:
    if items is None:
        return None
    seen: set[str] = set()
    out: list[McpImplementationFile] = []
    for item in items:
        path = (item.path or "").replace("\\", "/").lstrip("/")
        if not _IMPL_RE.match(path):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"非法 implementation 路径：{item.path}（须为 implementation/*.py）",
            )
        if path in seen:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"重复的 implementation 路径：{path}",
            )
        seen.add(path)
        out.append(McpImplementationFile(path=path, content=item.content or ""))
    return out


def _tools_summary(tools_json: Any) -> list[dict[str, str]]:
    rows = (
        tools_json.get("tools")
        if isinstance(tools_json, dict)
        else tools_json
        if isinstance(tools_json, list)
        else []
    )
    out: list[dict[str, str]] = []
    for item in rows or []:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        out.append(
            {
                "name": str(item.get("name")),
                "description": str(item.get("description") or ""),
            }
        )
    return out


_ENV_PLACEHOLDER_RE = re.compile(r"^\$\{[^}]+\}$")


def public_env_hint(key: str, value: Any = None) -> str:
    """对外展示用：仅保留 ${VAR} 占位，绝不回传明文密钥。"""
    text = "" if value is None else str(value).strip()
    if text and _ENV_PLACEHOLDER_RE.match(text):
        return text
    safe = re.sub(r"[^A-Za-z0-9_]", "_", str(key or "VAR")).strip("_") or "VAR"
    return f"${{{safe}}}"


def public_env_map(env: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(env, dict):
        return {}
    return {str(k): public_env_hint(str(k), v) for k, v in env.items()}


def _input_schema_from_connection(
    name: str,
    version: str,
    description: str,
    connection: dict[str, Any],
    tools_json: Any = None,
) -> dict[str, Any]:
    env = connection.get("env") if isinstance(connection.get("env"), dict) else {}
    return {
        "kind": "mcp",
        "name": name,
        "version": version,
        "description": description or "",
        "transport": connection.get("transport") or connection.get("type") or "stdio",
        "command": connection.get("command") or "",
        "args": list(connection.get("args") or [])
        if isinstance(connection.get("args"), list)
        else [],
        "url": connection.get("url") or "",
        "server": connection.get("server") or "",
        # headers 可能含 token，详情只暴露键名
        "header_keys": [
            str(k)
            for k in (
                connection.get("headers")
                if isinstance(connection.get("headers"), dict)
                else {}
            ).keys()
        ],
        "required_env": [str(k) for k in env.keys()],
        "env": public_env_map(env),
        "tools": _tools_summary(tools_json),
    }


async def _mcp_versions(db: AsyncSession, name: str) -> list[Capability]:
    rows = (
        await db.scalars(
            select(Capability)
            .options(selectinload(Capability.artifacts))
            .where(and_(Capability.name == name, Capability.type == "mcp"))
        )
    ).all()
    return list(rows)


async def get_editable(
    db: AsyncSession, user: User | None, name: str
) -> tuple[
    Capability, dict[str, Any], Any, list[McpImplementationFile], list[dict[str, Any]], str
]:
    versions = await _mcp_versions(db, name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"MCP {name} 不存在")
    draft = next(
        (
            c
            for c in versions
            if c.status in ("draft", "returned", "rejected")
            and (user is None or user.role == "admin" or c.author_id == user.id)
        ),
        None,
    )
    published = [c for c in versions if c.status in ("published", "deprecated")]
    cap = draft or (max(published, key=lambda c: parse_semver(c.version)) if published else None)
    if cap is None:
        cap = max(versions, key=lambda c: parse_semver(c.version))
    files = merge_edit_package_files(cap, published, core_text_files=_CORE_TEXT)
    connection = _loads(files.get("connection.json"), {})
    if not isinstance(connection, dict):
        connection = {}
    if not connection and isinstance(cap.input_schema, dict):
        schema = cap.input_schema
        if schema.get("kind") == "mcp" or schema.get("command") or schema.get("url") or schema.get("server"):
            connection = {
                "transport": schema.get("transport") or "stdio",
                "command": schema.get("command") or "",
                "url": schema.get("url") or "",
                "server": schema.get("server") or "",
            }
            if schema.get("required_env"):
                connection["env"] = {k: "" for k in schema["required_env"]}
    tools = _loads(files.get("tools.json"), None)
    implementations = _read_implementations(files)
    if not implementations:
        implementations = [
            McpImplementationFile(path=_DEFAULT_SERVER, content=_DEFAULT_SERVER_STUB)
        ]
    base_version = (
        max(published, key=lambda c: parse_semver(c.version)).version if published else ""
    )
    return cap, connection, tools, implementations, _file_list(files), base_version


def build_mcp_package(
    *,
    base: Capability | None,
    name: str,
    description: str,
    version: str,
    connection: dict[str, Any],
    tools_json: Any,
    category: str,
    tags: list[str],
    implementations: list[McpImplementationFile] | None = None,
) -> bytes:
    files: dict[str, bytes] = {}
    base_files = read_capability_files(base) if base is not None else {}
    for fname, content in base_files.items():
        if fname in _CORE:
            continue
        if implementations is not None and _is_impl(fname):
            continue
        files[fname] = content
    meta = _loads(base_files.get("mcp.json"), {}) or {}
    files["mcp.json"] = json.dumps(
        {
            "name": name,
            "description": description or meta.get("description", ""),
            "version": version,
            "category": category or meta.get("category", ""),
            "tags": tags if tags is not None else (meta.get("tags") or []),
        },
        ensure_ascii=False,
        indent=2,
    ).encode("utf-8")
    files["connection.json"] = json.dumps(connection, ensure_ascii=False, indent=2).encode("utf-8")
    if tools_json is not None:
        files["tools.json"] = json.dumps(tools_json, ensure_ascii=False, indent=2).encode("utf-8")
    elif "tools.json" in base_files:
        files["tools.json"] = base_files["tools.json"]
    if implementations is not None:
        for item in implementations:
            files[item.path] = item.content.encode("utf-8")
    if "security.json" not in files:
        files["security.json"] = b"{}\n"
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for fname, content in files.items():
            zf.writestr(fname, content)
    return buf.getvalue()


async def save_version(
    db: AsyncSession, user: User, name: str, data: McpEditSave
) -> tuple[Capability, dict[str, Any], Any, list[McpImplementationFile], list[dict[str, Any]]]:
    versions = await _mcp_versions(db, name)
    if not versions:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"MCP {name} 不存在")
    draft = next((c for c in versions if c.status in ("draft", "returned", "rejected")), None)
    published = [c for c in versions if c.status in ("published", "deprecated")]
    base = max(published, key=lambda c: parse_semver(c.version)) if published else None
    inherit = merge_edit_package_files(
        draft or base or versions[0], published, core_text_files=_CORE_TEXT
    )
    inherit_conn = _loads(inherit.get("connection.json"), {}) or {}
    if not isinstance(inherit_conn, dict):
        inherit_conn = {}
    inherit_impls = {f.path: f for f in _read_implementations(inherit)}

    connection = dict(data.connection or {})
    if not (
        connection.get("transport")
        or connection.get("command")
        or connection.get("url")
        or connection.get("server")
    ):
        connection = dict(inherit_conn or connection)
    validate_mcp_connection(connection, "connection.json")

    implementations = _normalize_implementations(data.implementations)
    if implementations is not None:
        filled: list[McpImplementationFile] = []
        for item in implementations:
            content = item.content
            if not str(content or "").strip() and item.path in inherit_impls:
                content = inherit_impls[item.path].content
            filled.append(McpImplementationFile(path=item.path, content=content or ""))
        if not filled and inherit_impls:
            filled = list(inherit_impls.values())
        implementations = filled

    if draft is not None:
        if user.role != "admin" and draft.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限编辑该草稿")
        cap = draft
    else:
        if base is None:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "该 MCP 没有已发布版本，无法创建新版本",
            )
        if user.role != "admin" and base.author_id != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "只能编辑自己发布的 MCP")
        new_version = data.new_version or next_version(base.version, "patch")
        exists = await db.scalar(
            select(Capability.id).where(
                and_(
                    Capability.name == name,
                    Capability.version == new_version,
                    Capability.type == "mcp",
                )
            )
        )
        if exists:
            raise HTTPException(status.HTTP_409_CONFLICT, f"MCP {name} 已存在版本 {new_version}")
        cap = Capability(
            name=name,
            description=data.description or base.description,
            type="mcp",
            version=new_version,
            category=data.category or base.category,
            tags=data.tags if data.tags is not None else list(base.tags or []),
            visibility=base.visibility,
            status="draft",
            author_id=user.id,
            organization=user.organization,
            **draft_policy_kwargs(base),
        )
        db.add(cap)
        await db.flush()

    if data.description:
        cap.description = data.description
    if data.category:
        cap.category = data.category
    if data.tags is not None:
        cap.tags = list(data.tags)

    pkg = build_mcp_package(
        base=package_base_for_save(draft, published) or base,
        name=cap.name,
        description=cap.description or "",
        version=cap.version,
        connection=connection,
        tools_json=data.tools_json,
        category=cap.category or "",
        tags=list(cap.tags or []),
        implementations=implementations,
    )
    info = get_storage().save(cap.id, f"{cap.name}-{cap.version}.zip", io.BytesIO(pkg))
    db.add(
        CapabilityArtifact(
            capability_id=cap.id, filename=f"{cap.name}-{cap.version}.zip", **info
        )
    )
    tools = data.tools_json
    if tools is None:
        with zipfile.ZipFile(io.BytesIO(pkg)) as zf:
            if "tools.json" in zf.namelist():
                tools = json.loads(zf.read("tools.json").decode("utf-8"))
    cap.input_schema = _input_schema_from_connection(
        cap.name, cap.version, cap.description or "", connection, tools
    )
    await db.commit()
    with zipfile.ZipFile(io.BytesIO(pkg)) as zf:
        pkg_files = {
            name: zf.read(name)
            for name in zf.namelist()
            if not name.endswith("/")
        }
    return (
        cap,
        connection,
        tools,
        _read_implementations(pkg_files),
        _file_list_from_zip(pkg),
    )
