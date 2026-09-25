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
    # 标准优先: skill 以 SKILL.md 为准(skill.json 可选); mcp 以 server.json 为准(旧 4 文件兼容)
    "skill": ["SKILL.md"],
    "mcp": ["server.json"],
    "workflow": ["workflow.json"],
    "plugin": [],
    "rule": ["rule.json", "RULE.mdc"],
    "command": ["command.json", "COMMAND.md"],
    "hook": ["hook.json", "hooks.json"],
}

# 旧格式 mcp 包(4 文件)兼容集合
_MCP_LEGACY_FILES = {"mcp.json", "connection.json", "tools.json", "security.json"}

OPTIONAL_FILES: dict[str, list[str]] = {
    "agent": ["TEAM.md", "tools.json", "knowledge/", "skills/", "examples/", "agents/", "dependencies.json"],
    "tool": ["security.json", "tests/", "examples/", "docs/", "implementation/__init__.py"],
    "skill": ["templates/", "assets/", "dependencies.json", "examples/", "scripts/", "references/"],
    "mcp": ["docker-compose.yml", "docs/", "implementation/"],
    "workflow": ["README.md", "examples/"],
    "plugin": [
        "agents/",
        "skills/",
        "tools/",
        "rules/",
        "commands/",
        "hooks/",
        "mcp.json",
        ".cursor-plugin/",
        "mcp/",
        "scripts/",
    ],
    "rule": ["examples/", "references/"],
    "command": ["examples/", "scripts/"],
    "hook": ["scripts/", "README.md"],
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


# ---- 标准优先解析(server.json / SKILL.md) ----
_STANDARD_SKILL_NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")  # agentskills.io: 小写连字符, 无首尾/连续连字符
_MCP_NAMESPACE_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*(/[a-z0-9][a-z0-9._-]*)?$")  # MCP registry: 命名空间 name


def _read_text_file(zf: zipfile.ZipFile, zip_name: str) -> str:
    try:
        return zf.read(zip_name).decode("utf-8-sig")
    except Exception:
        return ""


def _parse_frontmatter(text: str) -> dict[str, str]:
    """极简 frontmatter: 只取顶层 `key: value` 标量(与 agent/dashboard 加载器同构)。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}
    out: dict[str, str] = {}
    for line in lines[1:]:
        if line.strip() == "---":
            break
        m = re.match(r"^([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if m:
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def _skill_meta_from_md(zf: zipfile.ZipFile, files: dict[str, str]) -> dict[str, Any]:
    """从标准 SKILL.md frontmatter 构造内部 meta(兼容 skill.json 缺失)。"""
    fm = _parse_frontmatter(_read_text_file(zf, files["SKILL.md"]))
    name = (fm.get("name") or "").strip()
    if not name:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "SKILL.md 缺少 frontmatter name")
    if not _STANDARD_SKILL_NAME_RE.match(name):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"SKILL.md name 须为小写字母/数字/连字符(agentskills 规范): {name}",
        )
    meta: dict[str, Any] = {
        "name": name,
        "slug": name,
        "description": (fm.get("description") or "").strip(),
        "version": (fm.get("version") or "0.1.0"),
        "standard_name": name,
    }
    if fm.get("license"):
        meta["license"] = fm["license"]
    return meta


def _server_json_meta(server: dict[str, Any], label: str) -> dict[str, Any]:
    """标准 server.json → 内部 meta(name=命名空间 slug; title/description 作展示)。"""
    name = str(server.get("name") or "").strip()
    if not name:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"{label} 缺少 name")
    if not _MCP_NAMESPACE_RE.match(name):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            f"{label} name 须为 MCP 命名空间(小写, 可含 '.'/'_'/'-' 与可选 '/'), 如 io.github.x/y",
        )
    return {
        "name": name,
        "slug": name,
        "display_name": str(server.get("title") or server.get("display_name") or "").strip(),
        "description": str(server.get("description") or ""),
        "version": str(server.get("version") or "0.1.0"),
        "standard_name": name,
    }


def _server_json_connection(server: dict[str, Any]) -> dict[str, Any]:
    """标准 server.json → 内部 connection(优先 remotes; 否则记 packages 引用, 默认不本地执行)。"""
    remotes = server.get("remotes")
    if isinstance(remotes, list):
        for r in remotes:
            if isinstance(r, dict) and r.get("url"):
                conn: dict[str, Any] = {"transport": str(r.get("type") or "streamable-http"), "url": r["url"]}
                if isinstance(r.get("headers"), dict):
                    conn["headers"] = r["headers"]
                return conn
    pkgs = server.get("packages")
    if isinstance(pkgs, list) and pkgs and isinstance(pkgs[0], dict):
        return {"transport": "package", "package": pkgs[0], "command": ""}
    return {"transport": "stdio", "command": ""}


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


def _validate_skill_package(zf: zipfile.ZipFile, files: dict[str, str], meta: dict[str, Any],
                            label: str = "skill.json") -> None:
    validate_skill_meta(meta, label, require_version=True)


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

    # 必填文件: mcp 标准(server.json) 或旧格式(4 文件)二选一; 其余类型按 REQUIRED_FILES
    if capability_type == "mcp":
        if "server.json" not in names and not _MCP_LEGACY_FILES <= names:
            preview = "\u3001".join(sorted(names)[:12]) or "\u7a7a"
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "mcp \u5305\u7f3a\u5c11 server.json(\u6807\u51c6) \u6216 mcp.json/connection.json/tools.json/security.json(\u65e7\u683c\u5f0f)"
                f"\uff08\u5305\u5185\uff1a{preview}\uff09",
            )
    else:
        for required in REQUIRED_FILES.get(capability_type, []):
            if required not in names:
                preview = "\u3001".join(sorted(names)[:12]) or "\u7a7a"
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY,
                    f"{capability_type} \u5305\u7f3a\u5c11\u5fc5\u9700\u6587\u4ef6\uff1a{required}\uff08\u5305\u5185\uff1a{preview}\uff09",
                )

    # 标准优先: skill 以 SKILL.md(skill.json 可选) / mcp 以 server.json(旧格式兼容)
    _mcp_standard_connection: dict[str, Any] | None = None
    if capability_type == "skill":
        if "skill.json" in names:
            meta_file = "skill.json"
            meta = _read_json(zf, files[meta_file], meta_file)
            _resolve_meta_name(meta, meta_file)
            _check_name(str(meta["name"]), meta_file)
        else:
            meta_file = "SKILL.md"
            meta = _skill_meta_from_md(zf, files)
    elif capability_type == "mcp":
        if "server.json" in names:
            meta_file = "server.json"
            _server = _read_json(zf, files[meta_file], meta_file)
            meta = _server_json_meta(_server, meta_file)
            _mcp_standard_connection = _server_json_connection(_server)
        else:
            meta_file = "mcp.json"
            meta = _read_json(zf, files[meta_file], meta_file)
            _resolve_meta_name(meta, meta_file)
            _check_name(str(meta["name"]), meta_file)
    else:
        meta_file = {
            "agent": "agent.json",
            "tool": "tool.json",
            "workflow": "workflow.json",
            "rule": "rule.json",
            "command": "command.json",
            "hook": "hook.json",
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
        if _mcp_standard_connection is not None:
            details["connection"] = _mcp_standard_connection
            details["warnings"] = validate_mcp_connection(_mcp_standard_connection, "server.json")
        else:
            details["connection"] = _read_json(zf, files["connection.json"], "connection.json")
            details["warnings"] = _validate_mcp_package(zf, files, meta, details["connection"])
    elif capability_type == "tool":
        details["schema"] = validate_tool_schema(
            _read_json(zf, files["schema.json"], "schema.json"), "schema.json"
        )
    elif capability_type == "skill":
        _validate_skill_package(zf, files, meta, label=meta_file)
    elif capability_type == "agent":
        details["warnings"] = _validate_agent_embedded(zf, files, meta)
    elif capability_type in ("rule", "command"):
        validate_skill_meta(meta, meta_file, require_version=True)
    elif capability_type == "hook":
        validate_skill_meta(meta, "hook.json", require_version=True)
        hooks_cfg = _read_json(zf, files["hooks.json"], "hooks.json")
        if "hooks" not in hooks_cfg and "version" not in hooks_cfg:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                "hooks.json 需包含 hooks 对象（Cursor hooks 格式）",
            )
        details["hooks"] = hooks_cfg
    return details


_TEXT_EXTENSIONS = {
    ".md",
    ".mdc",
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


_TEMPLATE_KINDS = frozenset({"skill", "mcp", "tool", "agent", "plugin", "rule", "command", "hook"})


def build_package_template(kind: str, *, name: str = "example") -> bytes:
    """生成符合 REQUIRED_FILES 的空模板 zip，供小白下载改写。"""
    if kind not in _TEMPLATE_KINDS:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"不支持的模板类型：{kind}（可选 {sorted(_TEMPLATE_KINDS)}）",
        )
    safe = NAME_RE.match(name) and name or "example"
    files: dict[str, str] = {}
    if kind == "skill":
        files["skill.json"] = json.dumps(
            {"name": safe, "description": "示例技能", "version": "0.1.0"},
            ensure_ascii=False,
            indent=2,
        )
        files["SKILL.md"] = (
            f"# {safe}\n\n## 何时使用\n\n- …\n\n## 步骤\n\n1. …\n"
        )
    elif kind == "mcp":
        files["mcp.json"] = json.dumps(
            {"name": safe, "description": "示例连接器", "version": "0.1.0"},
            ensure_ascii=False,
            indent=2,
        )
        files["connection.json"] = json.dumps(
            {
                "name": safe,
                "transport": "stdio",
                "command": "python",
                "args": ["-m", "your_mcp_server"],
                "env": {"API_KEY": "${API_KEY}"},
                "enabled": False,
            },
            ensure_ascii=False,
            indent=2,
        )
        files["tools.json"] = json.dumps(
            {"tools": [{"name": "example_tool", "description": "示例工具"}]},
            ensure_ascii=False,
            indent=2,
        )
        files["security.json"] = json.dumps(
            {"notes": "密钥请用 ${VAR} 占位，勿硬编码"},
            ensure_ascii=False,
            indent=2,
        )
        files["README.md"] = (
            "# 连接器市场包规范\n\n"
            "必需：`mcp.json`、`connection.json`、`tools.json`、`security.json`。\n\n"
            "不能直接上传 agent 仓里的 `mcp-server.json` / `mcp-config.json`，"
            "请按本模板改写。\n"
        )
    elif kind == "tool":
        files["tool.json"] = json.dumps(
            {"name": safe, "description": "示例沙箱工具", "version": "0.1.0"},
            ensure_ascii=False,
            indent=2,
        )
        files["schema.json"] = json.dumps(
            {
                "type": "object",
                "properties": {"text": {"type": "string", "description": "输入"}},
                "required": ["text"],
            },
            ensure_ascii=False,
            indent=2,
        )
        files["implementation/tool.py"] = (
            "def run(params):\n"
            '    """云端沙箱入口。"""\n'
            '    return {"ok": True, "echo": params.get("text")}\n'
        )
    elif kind == "agent":
        files["agent.json"] = json.dumps(
            {"name": safe, "description": "示例 Agent", "version": "0.1.0"},
            ensure_ascii=False,
            indent=2,
        )
        files["PROMPT.md"] = f"# {safe}\n\n你是一个示例专家。\n"
        files["dependencies.json"] = "[]\n"
    elif kind == "plugin":
        files["plugin.json"] = json.dumps(
            {
                "name": safe,
                "description": "示例能力包：含 skill + mcp + rule/command/hook",
                "version": "0.1.0",
            },
            ensure_ascii=False,
            indent=2,
        )
        files[".cursor-plugin/plugin.json"] = json.dumps(
            {
                "name": safe,
                "description": "Cursor Plugin 清单",
                "version": "0.1.0",
            },
            ensure_ascii=False,
            indent=2,
        )
        files["skills/demo-skill/SKILL.md"] = "# demo-skill\n\n示例技能步骤。\n"
        files["skills/demo-skill/skill.json"] = json.dumps(
            {"name": "demo-skill", "description": "能力包内嵌技能", "version": "0.1.0"},
            ensure_ascii=False,
            indent=2,
        )
        files["rules/demo-rule.mdc"] = (
            "---\ndescription: 示例规则\nalwaysApply: false\n---\n\n"
            "示例：优先使用项目既有模式。\n"
        )
        files["commands/demo-command.md"] = (
            "---\nname: demo-command\ndescription: 示例斜杠命令\n---\n\n"
            "# 示例命令\n\n按步骤完成当前任务。\n"
        )
        files["hooks/hooks.json"] = json.dumps(
            {
                "version": 1,
                "hooks": {
                    "sessionStart": [{"command": "./scripts/session-start.sh"}]
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        files["scripts/session-start.sh"] = "#!/bin/sh\necho session start\n"
        files["mcp.json"] = json.dumps(
            {
                "mcpServers": {
                    "demo-mcp": {
                        "command": "python",
                        "args": ["-m", "your_mcp_server"],
                        "env": {"API_KEY": "${API_KEY}"},
                    }
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        files["README.md"] = (
            "# Plugin 模板（Cursor Plugin 兼容）\n\n"
            "上传后市场会拆出 skills / mcp / rules / commands / hooks（及可选 agents）子能力。\n"
            "零号员工可用整包；IDE 可识别 .cursor-plugin/plugin.json。\n"
        )
    elif kind == "rule":
        files["rule.json"] = json.dumps(
            {
                "name": safe,
                "description": "示例规则",
                "version": "0.1.0",
                "alwaysApply": False,
                "globs": "",
            },
            ensure_ascii=False,
            indent=2,
        )
        files["RULE.mdc"] = (
            "---\n"
            f"description: {safe}\n"
            "alwaysApply: false\n"
            "---\n\n"
            f"# {safe}\n\n在适用文件上遵循本规则。\n"
        )
    elif kind == "command":
        files["command.json"] = json.dumps(
            {"name": safe, "description": "示例斜杠命令", "version": "0.1.0"},
            ensure_ascii=False,
            indent=2,
        )
        files["COMMAND.md"] = f"# {safe}\n\n## 步骤\n\n1. …\n"
    elif kind == "hook":
        files["hook.json"] = json.dumps(
            {"name": safe, "description": "示例 Hooks", "version": "0.1.0"},
            ensure_ascii=False,
            indent=2,
        )
        files["hooks.json"] = json.dumps(
            {
                "version": 1,
                "hooks": {
                    "sessionStart": [{"command": "./scripts/session-start.sh"}]
                },
            },
            ensure_ascii=False,
            indent=2,
        )
        files["scripts/session-start.sh"] = "#!/bin/sh\necho session start\n"

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for path, content in files.items():
            zf.writestr(path, content if isinstance(content, bytes) else content.encode("utf-8"))
    return buf.getvalue()
