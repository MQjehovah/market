"""桌面端（Dashboard）消费投影：不改 connection.json，只在目录里给出可粘贴片段。

分类规则对齐 dashboard installer（stdio 无 env/包内脚本才算本机可拉起；
SSE 仅无自定义头时本机可用）。其余 MCP 指向能力级 HTTP 网关。
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

_SCRIPT_ARG = re.compile(r"\.(py|js|mjs|cjs|ts)$", re.I)


def dashboard_mcp_urls(name: str, version: str | None = None) -> dict[str, str]:
    ref = name
    if version:
        ref = f"{name}@{version}"
    enc = quote(ref, safe="")
    return {
        "sse_url": f"/api/mcp-gateway/relay/{enc}/sse",
        "stream_url": f"/api/mcp-gateway/relay/{enc}/stream",
        "messages_url": f"/api/mcp-gateway/relay/{enc}/messages",
    }


def _nonempty_str(v: Any) -> str | None:
    return v.strip() if isinstance(v, str) and v.strip() else None


def _env_empty(v: Any) -> bool:
    if v is None:
        return True
    if isinstance(v, dict):
        return len(v) == 0
    return False


def _cwd_empty(v: Any) -> bool:
    return v is None or (isinstance(v, str) and not v.strip())


def _args_ok(args: Any) -> bool:
    return args is None or (isinstance(args, list) and all(isinstance(a, str) for a in args))


def is_package_script_ref(arg: str) -> bool:
    """与 dashboard installer isPackageScriptRef 对齐：包内实现文件不能本机直接 spawn。"""
    norm = arg.replace("\\", "/")
    if arg.startswith("implementation/") or norm.startswith("implementation/"):
        return True
    if re.match(r"^[A-Za-z]:", norm) or norm.startswith("/"):
        return False
    if arg.startswith("-"):
        return False
    return bool(_SCRIPT_ARG.search(arg))


def classify_dashboard_mcp(conn: dict[str, Any] | None) -> str:
    """返回 local-stdio | local-sse | gateway-sse。"""
    conn = conn if isinstance(conn, dict) else {}
    transport = str(conn.get("transport") or conn.get("type") or "stdio").strip().lower()
    if transport == "stdio":
        command = _nonempty_str(conn.get("command"))
        args = conn.get("args")
        need_pkg = isinstance(args, list) and any(
            isinstance(a, str) and is_package_script_ref(a) for a in args
        )
        if (
            command
            and _env_empty(conn.get("env"))
            and _cwd_empty(conn.get("cwd"))
            and _args_ok(args)
            and not need_pkg
        ):
            return "local-stdio"
        return "gateway-sse"
    if transport == "sse":
        url = _nonempty_str(conn.get("url"))
        if url and _env_empty(conn.get("headers")):
            return "local-sse"
        return "gateway-sse"
    return "gateway-sse"


def _mcp_conn_from_schema(schema: dict[str, Any]) -> dict[str, Any]:
    return {
        "transport": schema.get("transport") or "stdio",
        "command": schema.get("command") or "",
        "args": list(schema.get("args") or []) if isinstance(schema.get("args"), list) else [],
        "url": schema.get("url") or "",
        "headers": (
            {k: "" for k in (schema.get("header_keys") or [])}
            if schema.get("header_keys")
            else {}
        ),
        "env": schema.get("env") if isinstance(schema.get("env"), dict) else {},
        "cwd": schema.get("cwd") or "",
        "server": schema.get("server") or "",
    }


def _tools_from_schema(schema: dict[str, Any]) -> list[dict[str, str]]:
    raw = schema.get("tools")
    rows = raw if isinstance(raw, list) else []
    out: list[dict[str, str]] = []
    for item in rows:
        if not isinstance(item, dict) or not item.get("name"):
            continue
        out.append(
            {
                "name": str(item.get("name")),
                "description": str(item.get("description") or ""),
            }
        )
    return out


def dashboard_projection(cap) -> dict[str, Any]:
    """按 kind 给出 Dashboard 可消费的最小投影（不打开 zip）。"""
    cap_type = getattr(cap, "type", "") or ""
    name = getattr(cap, "name", "") or ""
    schema = getattr(cap, "input_schema", None) or {}
    if not isinstance(schema, dict):
        schema = {}

    if cap_type == "mcp":
        conn = _mcp_conn_from_schema(schema)
        mode = classify_dashboard_mcp(conn)
        # distribution 元数据可强制走网关或本地
        dist = getattr(cap, "distribution", None) or "both"
        if dist == "remote" and mode.startswith("local-"):
            mode = "gateway-sse"
        if dist == "local" and mode == "gateway-sse":
            # 仅本地：仍给出网关 URL 作参考，但 mode 标注 local-preferred
            mode = "local-stdio" if (conn.get("command") or "") else mode
        version = getattr(cap, "version", None) or ""
        urls = dashboard_mcp_urls(name, version if dist == "remote" else None)
        mcp_frag: dict[str, Any] = {"name": name}
        if mode == "local-stdio":
            mcp_frag["command"] = conn.get("command") or ""
            if conn.get("args"):
                mcp_frag["args"] = list(conn["args"])
        elif mode == "local-sse":
            mcp_frag["url"] = conn.get("url") or ""
        else:
            mcp_frag["url"] = urls["sse_url"]
            mcp_frag["headers"] = {"Authorization": "Bearer ${SSO_ACCESS_TOKEN}"}

        required_env = [
            str(k)
            for k in (schema.get("required_env") or list((conn.get("env") or {}).keys()))
            if str(k).strip()
        ]
        header_keys = [str(k) for k in (schema.get("header_keys") or []) if str(k).strip()]
        env_hints = {
            str(k): str(v)
            for k, v in (schema.get("env") or {}).items()
            if str(k).strip()
        }
        for k in required_env:
            env_hints.setdefault(k, f"${{{k}}}")

        return {
            "mode": mode,
            "distribution": dist,
            "risk_default": getattr(cap, "risk_default", None) or "read",
            "data_domain": getattr(cap, "data_domain", None) or "",
            "required_env": required_env,
            "header_keys": header_keys,
            "env_hints": env_hints,
            "needs_credentials": bool(required_env or header_keys),
            "tools": _tools_from_schema(schema),
            "mcp": mcp_frag,
            **urls,
            "note": (
                "stdio 含 implementation/ 或 env 时默认走能力级网关；"
                "本地轨安装前请填写 required_env 后再启用。"
                if required_env
                else (
                    "stdio 含 implementation/ 或 env 时走能力级网关；"
                    "当前桌面安装器要求 SSE 无自定义头，Bearer 需由后续桌面版本或手工 mcp.json 注入。"
                )
            ),
        }

    if cap_type == "plugin":
        comps = schema.get("components") if isinstance(schema.get("components"), list) else []
        return {
            "mode": "plugin",
            "components": [
                {
                    "type": str(c.get("type") or ""),
                    "name": str(c.get("name") or ""),
                    "role": str(c.get("role") or ""),
                }
                for c in comps
                if isinstance(c, dict) and c.get("name") and c.get("type")
            ],
            "note": "能力包：拆已发布子能力；桌面尚未识别 plugin kind 时请按 components 分别安装。",
        }

    if cap_type == "skill":
        return {
            "mode": "local-skill",
            "path": f"localagent/skills/{name}/",
            "online": f"/api/runtime/skills/{quote(name, safe='')}/activate",
            "note": "员工装机走本地 path；模型线上网关用 online 返回 SKILL.md 文本。",
        }

    if cap_type == "agent":
        return {
            "mode": "persona",
            "path": f"localagent/agents/{name}/",
            "online": f"/api/runtime/agents/{quote(name, safe='')}/persona",
            "note": (
                "员工装机可走 path 或 online 拉 PROMPT.md（勿用 /api/agents/.../edit）；"
                "依赖 skill/mcp 需另装或走能力包/线上网关。"
            ),
        }

    if cap_type == "tool":
        return {
            "mode": "remote-tool",
            "invoke": f"/api/runtime/tools/{quote(name, safe='')}/invoke",
            "schema": schema.get("schema") or schema.get("parameters") or {},
            "note": "不下载 zip；桌面注册 market:<name> 或 marketplace_use_tool 线上 invoke。",
        }

    return {"mode": "unsupported"}


def attach_consumer_fields(data: dict[str, Any], cap) -> None:
    """写入 CapabilityOut 的 consumers / components（就地改 data）。"""
    proj = dashboard_projection(cap)
    data["consumers"] = {"dashboard": proj}
    if getattr(cap, "type", "") == "plugin":
        comps = (getattr(cap, "input_schema", None) or {}).get("components") or []
        if isinstance(comps, list):
            data["components"] = comps


def runtime_spec(cap) -> dict[str, Any]:
    """能力运行规格契约（设计文档 §3，单一来源）：两端只消费、不各自推导。

    cloud/local/recommended 由 distribution 推导；transport/command/args/url/env
    来自包内 connection.json —— 发布时已固化到 input_schema（与本模块投影同源，
    不打开 zip）。清单不可得时给缺省；env 只输出占位形式，不含明文。
    """
    dist = getattr(cap, "distribution", None) or "both"
    schema = getattr(cap, "input_schema", None) or {}
    if not isinstance(schema, dict):
        schema = {}
    # 仅 MCP 类清单才具备 connection 字段，避免把其它 kind 的 schema 误当连接配置
    has_conn = schema.get("kind") == "mcp" or any(
        k in schema for k in ("transport", "command", "url")
    )
    conn = _mcp_conn_from_schema(schema) if has_conn else {}
    env_hint = conn.get("env") if isinstance(conn.get("env"), dict) else {}
    tools = schema.get("tools")
    dependencies: list[dict[str, str]] = []
    comps = schema.get("components")
    if isinstance(comps, list):
        for item in comps:
            if isinstance(item, dict) and item.get("name") and item.get("type"):
                dependencies.append({"name": str(item["name"]), "type": str(item["type"])})
    return {
        "cloud": dist != "local",
        "local": dist != "remote",
        "recommended": "local" if dist == "local" else "cloud",
        "transport": str(conn.get("transport") or "").strip(),
        "command": str(conn.get("command") or ""),
        "args": [str(a) for a in (conn.get("args") or [])]
        if isinstance(conn.get("args"), list)
        else [],
        "url": str(conn.get("url") or ""),
        "env": {str(k): str(v) for k, v in env_hint.items() if str(k).strip()},
        "tool_count": len(tools) if isinstance(tools, list) else 0,
        "risk": getattr(cap, "risk_default", None) or "read",
        "dependencies": dependencies,
    }
