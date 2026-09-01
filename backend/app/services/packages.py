"""Capability package structure and validation."""

from __future__ import annotations

import io
import json
import re
import zipfile
from typing import Any

from fastapi import HTTPException, status

REQUIRED_FILES: dict[str, list[str]] = {
    "agent": ["agent.json", "PROMPT.md"],
    "tool": ["tool.json", "schema.json", "implementation/tool.py"],
    "skill": ["skill.json", "SKILL.md"],
    "mcp": ["mcp.json", "connection.json", "tools.json", "security.json"],
    "workflow": ["workflow.json"],
    "plugin": [],
}

OPTIONAL_FILES: dict[str, list[str]] = {
    "agent": ["TEAM.md", "tools.json", "knowledge/", "skills/", "examples/", "agents/", "dependencies.json"],
    "tool": ["security.json", "tests/", "examples/", "docs/", "implementation/__init__.py"],
    "skill": ["templates/", "assets/", "dependencies.json", "examples/", "scripts/", "references/"],
    "mcp": ["docker-compose.yml", "docs/"],
    "workflow": ["README.md", "examples/"],
    "plugin": ["agents/", "skills/", "tools/", "mcp.json", ".cursor-plugin/", "mcp/"],
}

# Allow Unicode letters (incl. CJK), digits, underscore, hyphen, dot
NAME_RE = re.compile(r"^[\w.\-]+$", re.UNICODE)

_IGNORED_ROOTS = {"__macosx", ".ds_store"}
_IGNORED_FILES = {".ds_store", "thumbs.db"}

# Prefer root README; then docs/; case-insensitive basename match
_README_CANDIDATES = (
    "README.md",
    "readme.md",
    "Readme.md",
    "docs/README.md",
    "docs/readme.md",
)


def extract_readme_text(content: bytes, *, max_chars: int = 200_000) -> str:
    """Extract README markdown from a capability zip, if present."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile:
        return ""
    files = zip_file_map(zf)
    if not files:
        return ""
    # Exact preferred paths first
    for cand in _README_CANDIDATES:
        raw = files.get(cand)
        if raw is not None:
            text = zf.read(raw).decode("utf-8", errors="replace").strip()
            return text[:max_chars] if text else ""
    # Any *readme*.md at package root (depth 1)
    root_readmes = sorted(
        (logical, raw)
        for logical, raw in files.items()
        if "/" not in logical and logical.lower().endswith(".md") and "readme" in logical.lower()
    )
    if root_readmes:
        text = zf.read(root_readmes[0][1]).decode("utf-8", errors="replace").strip()
        return text[:max_chars] if text else ""
    return ""


def _norm_zip_name(name: str) -> str:
    return name.replace("\\", "/").lstrip("/")


def _ignored_zip_entry(name: str) -> bool:
    parts = [p for p in _norm_zip_name(name).split("/") if p]
    if not parts:
        return True
    if parts[0].lower() in _IGNORED_ROOTS:
        return True
    return parts[-1].lower() in _IGNORED_FILES


def zip_file_map(zf: zipfile.ZipFile) -> dict[str, str]:
    """Map logical path -> zip member. Strip single top-level folder if present."""
    files: dict[str, str] = {}
    for raw in zf.namelist():
        name = _norm_zip_name(raw)
        if not name or name.endswith("/") or _ignored_zip_entry(name):
            continue
        files[name] = raw
    if not files:
        return {}
    firsts = {name.split("/", 1)[0] for name in files}
    if len(firsts) == 1 and all("/" in name for name in files):
        prefix = next(iter(firsts)) + "/"
        return {name[len(prefix) :]: raw for name, raw in files.items()}
    return files


def flatten_zip(content: bytes, files: dict[str, str]) -> bytes:
    """Rewrite wrapped zip entries to logical relative paths."""
    if all(_norm_zip_name(raw) == logical for logical, raw in files.items()):
        return content
    buf = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(content)) as src, zipfile.ZipFile(
        buf, "w", compression=zipfile.ZIP_DEFLATED
    ) as dst:
        for logical, raw in files.items():
            dst.writestr(logical, src.read(raw))
    return buf.getvalue()


def prepare_package(capability_type: str, content: bytes) -> tuple[bytes, dict[str, Any]]:
    """Validate package; flatten single top-level folder when present."""
    details = validate_package(capability_type, content)
    flat = flatten_zip(content, details["files"])
    with zipfile.ZipFile(io.BytesIO(flat)) as zf:
        details["files"] = {
            _norm_zip_name(n): n for n in zf.namelist() if n and not n.endswith("/")
        }
    return flat, details


def _read_json(zf: zipfile.ZipFile, zip_name: str, logical_name: str) -> dict[str, Any]:
    try:
        data = json.loads(zf.read(zip_name).decode("utf-8-sig"))
    except Exception as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"\u6587\u4ef6 {logical_name} \u4e0d\u662f\u5408\u6cd5\u7684 UTF-8 JSON\uff1a{exc}",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"\u6587\u4ef6 {logical_name} \u9876\u5c42\u5fc5\u987b\u662f JSON \u5bf9\u8c61",
        )
    return data


def _resolve_meta_name(meta: dict[str, Any], meta_file: str) -> str:
    """Accept top-level name or identity.name (v3.1)."""
    name = meta.get("name")
    if not name and isinstance(meta.get("identity"), dict):
        name = meta["identity"].get("name")
        if name and "name" not in meta:
            meta["name"] = name
        ver = meta["identity"].get("version")
        if ver and "version" not in meta:
            meta["version"] = ver
    if not name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{meta_file} \u7f3a\u5c11 name \u5b57\u6bb5\uff08\u6216 identity.name\uff09",
        )
    return str(name)


def _check_name(name: str, label: str) -> None:
    if not name or not NAME_RE.match(name):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{label} \u540d\u79f0\u683c\u5f0f\u65e0\u6548\uff1a{name}\uff08\u4ec5\u5141\u8bb8\u5b57\u6bcd/\u6570\u5b57/\u4e0b\u5212\u7ebf/\u8fde\u5b57\u7b26/\u70b9\uff09",
        )


def _warn_hardcoded_env(env: Any, label: str) -> list[str]:
    """Return warnings for values that look like hardcoded secrets (密钥应出制品)。"""
    warnings: list[str] = []
    if not isinstance(env, dict):
        return warnings
    secretish = ("key", "token", "secret", "password", "passwd", "authorization", "api_key")
    for key, val in env.items():
        if not isinstance(val, str):
            continue
        if val.startswith("${") or val.startswith("{"):
            continue
        key_l = str(key).lower()
        looks_secret = any(s in key_l for s in secretish)
        if looks_secret or len(val) >= 8:
            warnings.append(
                f"{label}.{key} 可能是硬编码敏感值，制品内请使用 ${{VAR}} / ${{VAR:default}} 占位"
            )
    return warnings


def validate_skill_meta(meta: dict[str, Any], label: str = "skill.json", *, require_version: bool = True) -> str:
    """共享 skill 元数据校验；返回规范化 name。"""
    name = _resolve_meta_name(meta, label)
    _check_name(name, label)
    identity = meta.get("identity") if isinstance(meta.get("identity"), dict) else None
    has_version = bool(meta.get("version") or (identity and identity.get("version")))
    if require_version and not has_version:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{label} 缺少 identity.version（或顶层 version）",
        )
    return name


def validate_mcp_connection(connection: dict[str, Any], label: str = "connection.json") -> list[str]:
    """共享 MCP connection 校验；返回 warnings（含 env / headers 密钥占位检查）。"""
    if not isinstance(connection, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{label} 必须是 JSON 对象")
    warnings = _warn_hardcoded_env(connection.get("env"), f"{label}.env")
    warnings.extend(_warn_hardcoded_env(connection.get("headers"), f"{label}.headers"))
    transport = connection.get("transport") or connection.get("type")
    if transport in ("stdio", None) and not connection.get("command") and not connection.get("url") and not connection.get("server"):
        if connection.get("url") or connection.get("server"):
            return warnings
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{label} 需包含 command、url 或 server（gateway）之一",
        )
    if transport in ("http", "sse", "streamable_http", "streamable-http") and not connection.get("url"):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{label} transport={transport} 时需要 url",
        )
    return warnings


def validate_tool_schema(schema: Any, label: str = "schema.json") -> dict[str, Any]:
    """共享 tool schema 校验。"""
    if not isinstance(schema, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{label} \u9876\u5c42\u5fc5\u987b\u662f JSON \u5bf9\u8c61")
    return schema


def _validate_agent_embedded(zf: zipfile.ZipFile, files: dict[str, str], meta: dict[str, Any]) -> list[str]:
    """Extra checks for agent packages: skill dirs, mcp config, name uniqueness."""
    warnings: list[str] = []
    skill_names: list[str] = []

    for item in meta.get("skills") or []:
        if isinstance(item, dict) and item.get("name"):
            name = str(item["name"])
            _check_name(name, "skills[]")
            if name in skill_names:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"agent.json skills \u540d\u79f0\u91cd\u590d\uff1a{name}",
                )
            skill_names.append(name)

    for logical in files:
        if logical.startswith("skills/") and logical.endswith("/SKILL.md"):
            folder = logical[len("skills/") : -len("/SKILL.md")]
            if folder and "/" not in folder:
                if folder in skill_names:
                    continue
                _check_name(folder, f"skills/{folder}")
                skill_names.append(folder)
        if logical.startswith("skills/") and logical.endswith("/skill.json"):
            folder = logical[len("skills/") :].rsplit("/", 1)[0]
            skill_md = f"skills/{folder}/SKILL.md"
            if skill_md not in files and f"skills/{folder.split('/')[0]}/SKILL.md" not in files:
                # only require SKILL.md for single-level skill dirs
                if "/" not in folder and f"skills/{folder}/SKILL.md" not in files:
                    raise HTTPException(
                        status.HTTP_422_UNPROCESSABLE_ENTITY,
                        f"skills/{folder}/ \u7f3a\u5c11 SKILL.md",
                    )
            sj = _read_json(zf, files[logical], logical)
            sname = validate_skill_meta(sj, logical, require_version=True)
            if sname and sname not in skill_names:
                skill_names.append(sname)

    mcp_names: list[str] = []
    for srv in meta.get("mcp_servers") or []:
        if isinstance(srv, dict) and srv.get("name"):
            name = str(srv["name"])
            _check_name(name, "mcp_servers[]")
            if name in mcp_names:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"mcp_servers \u540d\u79f0\u91cd\u590d\uff1a{name}",
                )
            mcp_names.append(name)
            warnings.extend(_warn_hardcoded_env(srv.get("env"), f"mcp_servers[{name}]"))
            # 内联声明：有 command 时按 connection 规则校验
            if srv.get("command") or srv.get("url") or srv.get("server"):
                warnings.extend(validate_mcp_connection(srv, f"mcp_servers[{name}]"))

    mcp_cfg = meta.get("mcp") if isinstance(meta.get("mcp"), dict) else {}
    for srv in mcp_cfg.get("required_servers") or []:
        if isinstance(srv, dict) and srv.get("name"):
            name = str(srv["name"])
            if name in mcp_names:
                continue
            _check_name(name, "mcp.required_servers[]")
            mcp_names.append(name)

    for cfg_path in ("mcp/config.json", "mcp/servers.json"):
        if cfg_path not in files:
            continue
        cfg = _read_json(zf, files[cfg_path], cfg_path)
        servers = cfg.get("mcpServers")
        if not isinstance(servers, dict):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{cfg_path} \u9700\u5305\u542b mcpServers \u5bf9\u8c61",
            )
        for name, srv in servers.items():
            _check_name(str(name), cfg_path)
            if not isinstance(srv, dict):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"{cfg_path} \u4e2d server '{name}' \u683c\u5f0f\u65e0\u6548",
                )
            warnings.extend(validate_mcp_connection(srv, f"{cfg_path}[{name}]"))
            if str(name) not in mcp_names:
                mcp_names.append(str(name))

    return warnings


def _validate_skill_package(zf: zipfile.ZipFile, files: dict[str, str], meta: dict[str, Any]) -> None:
    validate_skill_meta(meta, "skill.json", require_version=True)


def _validate_mcp_package(zf: zipfile.ZipFile, files: dict[str, str], meta: dict[str, Any], connection: dict[str, Any]) -> list[str]:
    _resolve_meta_name(meta, "mcp.json")
    _check_name(str(meta["name"]), "mcp.json")
    return validate_mcp_connection(connection, "connection.json")


def validate_package(capability_type: str, content: bytes) -> dict[str, Any]:
    """Validate zip package. Returns parsed metadata."""
    try:
        zf = zipfile.ZipFile(io.BytesIO(content))
    except zipfile.BadZipFile as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "\u4e0a\u4f20\u6587\u4ef6\u4e0d\u662f\u6709\u6548\u7684 zip \u5305",
        ) from exc

    files = zip_file_map(zf)
    names = set(files)

    if capability_type == "plugin":
        from app.services.plugins import extract_plugin_components, resolve_plugin_meta

        logical_bytes = {logical: zf.read(raw) for logical, raw in files.items()}
        _, meta = resolve_plugin_meta(logical_bytes)
        if meta.get("name"):
            _check_name(str(meta["name"]), "plugin.json")
        components = extract_plugin_components(logical_bytes, meta)
        # duplicate component names within type
        seen: set[tuple[str, str]] = set()
        for comp in components:
            key = (comp["type"], comp["name"])
            if key in seen:
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"plugin \u5185\u90e8\u7ec4\u4ef6\u540d\u79f0\u91cd\u590d\uff1a{comp['type']}:{comp['name']}",
                )
            seen.add(key)
            _check_name(str(comp["name"]), f"plugin {comp['type']}")
        return {"meta": meta, "files": files, "components": components}

    for required in REQUIRED_FILES.get(capability_type, []):
        if required not in names:
            preview = "\u3001".join(sorted(names)[:12]) or "\u7a7a"
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"{capability_type} \u5305\u7f3a\u5c11\u5fc5\u9700\u6587\u4ef6\uff1a{required}\uff08\u5305\u5185\uff1a{preview}\uff09",
            )

    meta_file = {
        "agent": "agent.json",
        "tool": "tool.json",
        "skill": "skill.json",
        "mcp": "mcp.json",
        "workflow": "workflow.json",
    }[capability_type]
    meta = _read_json(zf, files[meta_file], meta_file)
    _resolve_meta_name(meta, meta_file)
    _check_name(str(meta["name"]), meta_file)

    details: dict[str, Any] = {"meta": meta, "files": files, "warnings": []}
    if capability_type == "workflow":
        if not isinstance(meta.get("nodes"), list) or not meta["nodes"]:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "workflow.json \u5fc5\u987b\u5305\u542b\u975e\u7a7a nodes \u5217\u8868",
            )
        for node in meta["nodes"]:
            if not isinstance(node, dict) or not node.get("id") or not node.get("type"):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    "workflow.json \u6bcf\u4e2a\u8282\u70b9\u5fc5\u987b\u5305\u542b id \u4e0e type",
                )
    if capability_type == "mcp":
        details["connection"] = _read_json(zf, files["connection.json"], "connection.json")
        details["warnings"] = _validate_mcp_package(zf, files, meta, details["connection"])
    elif capability_type == "tool":
        details["schema"] = validate_tool_schema(
            _read_json(zf, files["schema.json"], "schema.json"), "schema.json"
        )
    elif capability_type == "skill":
        _validate_skill_package(zf, files, meta)
    elif capability_type == "agent":
        details["warnings"] = _validate_agent_embedded(zf, files, meta)
    return details


_TEXT_EXTENSIONS = {
    ".md",
    ".markdown",
    ".txt",
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".xml",
    ".html",
    ".htm",
    ".css",
    ".scss",
    ".js",
    ".jsx",
    ".ts",
    ".tsx",
    ".py",
    ".sh",
    ".bash",
    ".zsh",
    ".ps1",
    ".bat",
    ".cmd",
    ".sql",
    ".env",
    ".ini",
    ".cfg",
    ".conf",
    ".gitignore",
    ".dockerignore",
    ".editorconfig",
    ".csv",
    ".tsv",
    ".log",
    ".rst",
    ".svg",
}
_TEXT_BASENAMES = {
    "dockerfile",
    "makefile",
    "license",
    "licence",
    "authors",
    "changelog",
    "gemfile",
    "procfile",
}
_MAX_PREVIEW_BYTES = 512_000


def _is_text_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    lower = name.lower()
    if lower in _TEXT_BASENAMES:
        return True
    if "." not in name:
        return False
    ext = "." + lower.rsplit(".", 1)[-1]
    return ext in _TEXT_EXTENSIONS


def _safe_logical_path(path: str) -> str:
    raw = (path or "").replace("\\", "/").strip()
    if not raw or raw.startswith("/") or ".." in raw.split("/"):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, "非法文件路径")
    return raw.lstrip("./")


def list_package_entries(content: bytes) -> list[dict[str, Any]]:
    """List logical files in a capability zip (flat, sorted)."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        files = zip_file_map(zf)
        out: list[dict[str, Any]] = []
        for logical, raw_name in sorted(files.items()):
            info = zf.getinfo(raw_name)
            out.append(
                {
                    "path": logical,
                    "size": int(info.file_size),
                    "text": _is_text_path(logical),
                }
            )
        return out


def read_package_entry(content: bytes, path: str, *, max_bytes: int = _MAX_PREVIEW_BYTES) -> dict[str, Any]:
    """Read one logical file for preview."""
    logical = _safe_logical_path(path)
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        files = zip_file_map(zf)
        if logical not in files:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"包内不存在文件：{logical}")
        raw_name = files[logical]
        info = zf.getinfo(raw_name)
        size = int(info.file_size)
        data = zf.read(raw_name)
        truncated = False
        if len(data) > max_bytes:
            data = data[:max_bytes]
            truncated = True
        is_text = _is_text_path(logical)
        if is_text:
            # Reject obvious binary even for "text" extensions
            if b"\x00" in data[:4096]:
                is_text = False
        result: dict[str, Any] = {
            "path": logical,
            "size": size,
            "truncated": truncated,
            "binary": not is_text,
            "content": "",
            "encoding": "",
        }
        if not is_text:
            return result
        for enc in ("utf-8", "utf-8-sig", "gb18030", "latin-1"):
            try:
                result["content"] = data.decode(enc)
                result["encoding"] = enc
                break
            except UnicodeDecodeError:
                continue
        else:
            result["binary"] = True
            result["content"] = ""
        return result
