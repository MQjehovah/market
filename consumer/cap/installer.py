"""把能力层的 Agent 及其依赖下载并组装成本地 Agent 配置。

产物结构（默认安装到 <agent-root>/config/agents/<name>/）：
    PROMPT.md / TEAM.md / skills/ / agents/   来自能力包（人设与附属资产）
    tools/<safe>.py                            依赖工具的云端桥接 BuiltinTool
    mcp_servers.json                           依赖 MCP 连接配置（凭据保留 ${VAR} 占位）
    installed.json                             安装清单（来源版本、时间、依赖）

组装完成后，可通过 `cap run <name> --mode local` 或本地 A2A 服务直接运行。
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .client import MarketClient


def extract_zip(content: bytes) -> dict[str, bytes]:
    """把能力包 zip 展开为 {相对路径: 内容}，跳过目录项。"""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        return {
            name: zf.read(name)
            for name in zf.namelist()
            if not name.endswith("/")
        }


def read_dependencies(files: dict[str, bytes]) -> list[dict[str, str]]:
    """从能力包读取依赖清单（dependencies.json 优先，agent.json 兜底）。"""
    for name in ("dependencies.json", "agent.json"):
        raw = files.get(name)
        if not raw:
            continue
        try:
            data = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            continue
        deps = data if isinstance(data, list) else data.get("dependencies", [])
        if isinstance(deps, list):
            return [
                {
                    "name": str(d.get("name", "")),
                    "type": str(d.get("type", "")),
                    "version": str(d.get("version", "") or ""),
                }
                for d in deps
                if d.get("name") and d.get("type")
            ]
    return []


def safe_module_name(name: str) -> str:
    """把能力名转成安全的 Python 模块名（保留可读前缀 + 短哈希）。"""
    import hashlib

    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]
    stem = re.sub(r"[^0-9A-Za-z_]", "_", name).strip("_") or "tool"
    return f"{stem}_{digest}"


def _merge_mcp_config(existing: list[dict[str, Any]], incoming: dict[str, Any]) -> list[dict[str, Any]]:
    """按 name 合并 MCP 连接配置，避免重复。"""
    if not isinstance(existing, list):
        existing = []
    name = incoming.get("name", "")
    result = [c for c in existing if c.get("name") != name]
    result.append(incoming)
    return result


_PLACEHOLDER_RE = re.compile(r"^\$\{([^}:]+)(?::[^}]*)?\}$")


def _is_placeholder(value: Any) -> bool:
    if value is None:
        return True
    text = str(value).strip()
    return text == "" or bool(_PLACEHOLDER_RE.match(text))


def required_env_keys(conn: dict[str, Any]) -> list[str]:
    """从 connection.env 提取待填密钥名。"""
    env = conn.get("env")
    if not isinstance(env, dict):
        return []
    return [str(k) for k in env.keys() if str(k).strip()]


def apply_env_overrides(conn: dict[str, Any], overrides: dict[str, str] | None) -> dict[str, Any]:
    """把 KEY=VALUE 覆盖写入 connection.env（保留其它字段）。"""
    if not overrides:
        return conn
    out = dict(conn)
    env = dict(out.get("env") or {}) if isinstance(out.get("env"), dict) else {}
    for key, val in overrides.items():
        k = str(key).strip()
        if not k:
            continue
        env[k] = str(val)
    out["env"] = env
    return out


def prompt_mcp_credentials(
    conn: dict[str, Any],
    *,
    interactive: bool = True,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """安装时提醒并帮助填写 env；交互失败或非 TTY 时保留占位。

    - 有 --env 覆盖则先写入
    - interactive + TTY：逐项提示（密码类用 getpass）
    - 全部填齐后询问是否 enabled=true
    """
    import getpass
    import sys

    out = apply_env_overrides(dict(conn), env_overrides)
    keys = required_env_keys(out)
    if not keys:
        return out

    env = dict(out.get("env") or {})
    pending = [k for k in keys if _is_placeholder(env.get(k))]
    if pending:
        print(
            f"\n[凭据] 该 MCP 需配置 {len(pending)} 个环境变量后才能启用："
            f" {', '.join(pending)}"
        )
    if interactive and pending and sys.stdin.isatty():
        secretish = ("password", "secret", "token", "key", "passwd", "credential")
        for key in pending:
            hint = env.get(key) or f"${{{key}}}"
            label = f"  {key}（占位 {hint}）: "
            try:
                if any(s in key.lower() for s in secretish):
                    val = getpass.getpass(label)
                else:
                    val = input(label)
            except (EOFError, KeyboardInterrupt):
                print("\n[凭据] 已跳过交互填写，保留占位；请稍后编辑 mcp_servers.json")
                break
            if val is None or str(val).strip() == "":
                print(f"  …跳过 {key}")
                continue
            env[key] = str(val).strip()
        out["env"] = env

    still = [k for k in keys if _is_placeholder(out.get("env", {}).get(k))]
    if still:
        print(
            f"[凭据] 仍有未填项：{', '.join(still)}。"
            "请编辑 mcp_servers.json 填入真实值后将 enabled 设为 true。"
        )
        out["enabled"] = False
        return out

    if interactive and sys.stdin.isatty():
        try:
            ans = input("  凭据已齐，是否立即启用该 MCP？[y/N] ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = ""
        out["enabled"] = ans in ("y", "yes")
        if out["enabled"]:
            print("[凭据] 已启用（enabled=true）")
        else:
            print("[凭据] 已写入凭据，enabled=false；确认无误后手动改为 true")
    else:
        # 非交互但通过 --env 填齐：仍默认不自动启用，避免 CI 误开
        out.setdefault("enabled", False)
        print("[凭据] 环境变量已写入；默认 enabled=false，确认后请手动启用")
    return out


def generate_tool_bridge(
    *,
    name: str,
    description: str,
    schema: dict[str, Any],
    base_url: str,
    module_name: str,
) -> str:
    """生成云端工具桥接工具：本地 Agent 调用时转发到能力层沙箱真实执行。"""
    schema_json = json.dumps(schema or {"type": "object", "properties": {}}, ensure_ascii=False, indent=4)
    name_lit = json.dumps(name, ensure_ascii=False)
    desc_lit = json.dumps(description or f"能力层工具 {name}", ensure_ascii=False)
    return f'''# 由能力层消费者生成：把云端工具桥接到本地 Agent（工具在云端沙箱真实执行）。
import json
import os
from urllib.parse import quote

from tools import BuiltinTool

_API = os.environ.get("CAP_URL", "{base_url}").rstrip("/")
_TOKEN = os.environ.get("CAP_TOKEN", "")
_NAME = {name_lit}
_DESC = {desc_lit}
_SCHEMA = {schema_json}


class MarketToolBridge(BuiltinTool):
    @property
    def name(self) -> str:
        return _NAME

    @property
    def description(self) -> str:
        return _DESC

    @property
    def parameters(self) -> dict:
        return _SCHEMA

    async def execute(self, **kwargs) -> str:
        try:
            import httpx

            async with httpx.AsyncClient(timeout=120) as _client:
                _resp = await _client.post(
                    f"{{_API}}/api/runtime/tools/{{quote(_NAME, safe='')}}/invoke",
                    headers={{"Authorization": f"Bearer {{_TOKEN}}"}},
                    json={{"params": kwargs}},
                )
                return _resp.text
        except Exception as _exc:  # noqa: BLE001
            return json.dumps({{"ok": False, "error": str(_exc)}}, ensure_ascii=False)
'''


def install_agent(
    client: MarketClient,
    name: str,
    *,
    version: str = "",
    target: str | Path,
    dry_run: bool = False,
    interactive: bool = True,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """下载 Agent 包与依赖并组装到 target/agents/<name>/，返回安装清单。"""
    target_path = Path(target).resolve()
    agent_dir = target_path / "agents" / name

    if dry_run:
        print(f"[dry-run] 将下载 Agent「{name}」v{version or 'latest'} 并组装到 {agent_dir}")

    agent_bytes, _ = client.download(name, version)
    agent_files = extract_zip(agent_bytes)
    deps = read_dependencies(agent_files)
    print(
        f"[install] 已下载 Agent「{name}」v{version or 'latest'}，"
        f"包内文件 {len(agent_files)} 个，依赖 {len(deps)} 个"
    )

    if dry_run:
        for d in deps:
            print(f"  [dry-run] 依赖 {d['type']}:{d['name']} v{d.get('version') or 'latest'}")
        return {"name": name, "dry_run": True, "dependencies": deps}

    agent_dir.mkdir(parents=True, exist_ok=True)
    for rel, content in agent_files.items():
        if rel.startswith("tools/") or rel.startswith("mcp/"):
            continue  # 依赖会单独处理；内嵌快照仅作记录
        out = agent_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(content)

    installed_deps: list[dict[str, Any]] = []
    tools_dir = agent_dir / "tools"
    mcp_file = agent_dir / "mcp_servers.json"
    mcp_configs: list[dict[str, Any]] = []
    if mcp_file.is_file():
        try:
            mcp_configs = json.loads(mcp_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            mcp_configs = []

    for dep in deps:
        dep_name, dep_type, dep_version = dep["name"], dep["type"], dep.get("version") or ""
        record: dict[str, Any] = {"name": dep_name, "type": dep_type, "version": dep_version}
        try:
            dep_bytes, _ = client.download(dep_name, dep_version)
            dep_files = extract_zip(dep_bytes)
        except Exception as exc:  # noqa: BLE001
            record["installed"] = False
            record["note"] = f"下载失败：{exc}"
            installed_deps.append(record)
            print(f"  [skip] 依赖 {dep_type}:{dep_name} 下载失败：{exc}")
            continue

        if dep_type == "skill":
            skill_dir = agent_dir / "skills" / dep_name
            for rel, content in dep_files.items():
                if rel in ("skill.json",):
                    continue
                out = skill_dir / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(content)
            record["installed"] = True
            print(f"  [ok] skill:{dep_name} -> skills/{dep_name}/")

        elif dep_type in ("rule", "command", "hook"):
            folder = {"rule": "rules", "command": "commands", "hook": "hooks"}[dep_type]
            skip = {"rule": "rule.json", "command": "command.json", "hook": "hook.json"}[dep_type]
            dest = agent_dir / folder / dep_name
            for rel, content in dep_files.items():
                if rel == skip:
                    continue
                out = dest / rel
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_bytes(content)
            record["installed"] = True
            print(f"  [ok] {dep_type}:{dep_name} -> {folder}/{dep_name}/")

        elif dep_type == "mcp":
            conn_file = dep_files.get("connection.json")
            if conn_file is not None:
                try:
                    conn = json.loads(conn_file.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    conn = {}
                conn.setdefault("name", dep_name)
                conn.setdefault("enabled", False)
                conn = prompt_mcp_credentials(
                    conn, interactive=interactive, env_overrides=env_overrides
                )
                mcp_configs = _merge_mcp_config(mcp_configs, conn)
            record["installed"] = True
            print(f"  [ok] mcp:{dep_name} -> mcp_servers.json")

        elif dep_type == "tool":
            try:
                schema = json.loads(dep_files.get("schema.json", b"{}").decode("utf-8"))
            except (ValueError, UnicodeDecodeError):
                schema = {}
            desc = dep_name
            try:
                meta = json.loads(dep_files.get("tool.json", b"{}").decode("utf-8"))
                desc = meta.get("description") or dep_name
            except (ValueError, UnicodeDecodeError):
                pass
            module_name = safe_module_name(dep_name)
            bridge = generate_tool_bridge(
                name=dep_name,
                description=desc,
                schema=schema,
                base_url=client.base_url,
                module_name=module_name,
            )
            tools_dir.mkdir(parents=True, exist_ok=True)
            (tools_dir / f"{module_name}.py").write_text(bridge, encoding="utf-8")
            record["installed"] = True
            record["module"] = f"tools/{module_name}.py"
            print(f"  [ok] tool:{dep_name} -> tools/{module_name}.py（云端桥接）")

        else:
            record["installed"] = False
            record["note"] = f"不支持的依赖类型：{dep_type}"
        installed_deps.append(record)

    if mcp_configs:
        mcp_file.write_text(
            json.dumps(mcp_configs, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    manifest = {
        "name": name,
        "type": "agent",
        "version": version or "latest",
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "source": {"url": client.base_url, "name": name, "version": version},
        "dependencies": installed_deps,
    }
    (agent_dir / "installed.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[install] 完成：{agent_dir}")
    return manifest


def install_skill(
    client: MarketClient,
    name: str,
    *,
    version: str = "",
    target: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """独立安装 skill 到 target/skills/<name>/。"""
    target_path = Path(target).resolve()
    skill_dir = target_path / "skills" / name
    if dry_run:
        print(f"[dry-run] 将下载 skill「{name}」v{version or 'latest'} 到 {skill_dir}")
        return {"name": name, "type": "skill", "dry_run": True}

    content, headers = client.download(name, version, cap_type="skill")
    files = extract_zip(content)
    skill_dir.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        if rel == "skill.json":
            continue
        out = skill_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    manifest = {
        "name": name,
        "type": "skill",
        "version": headers.get("x-capability-version") or version or "latest",
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "path": str(skill_dir),
    }
    (skill_dir / "installed.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[install] skill:{name} -> {skill_dir}")
    return manifest


def install_mcp(
    client: MarketClient,
    name: str,
    *,
    version: str = "",
    target: str | Path,
    dry_run: bool = False,
    interactive: bool = True,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """独立安装 mcp：合并 connection.json 到 target/mcp_servers.json。"""
    target_path = Path(target).resolve()
    mcp_file = target_path / "mcp_servers.json"
    if dry_run:
        print(f"[dry-run] 将下载 mcp「{name}」并合并到 {mcp_file}")
        return {"name": name, "type": "mcp", "dry_run": True}

    content, headers = client.download(name, version, cap_type="mcp")
    files = extract_zip(content)
    conn: dict[str, Any] = {}
    raw = files.get("connection.json")
    if raw is not None:
        try:
            conn = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError):
            conn = {}
    conn.setdefault("name", name)
    conn.setdefault("enabled", False)
    conn = prompt_mcp_credentials(
        conn, interactive=interactive, env_overrides=env_overrides
    )

    existing: list[dict[str, Any]] = []
    if mcp_file.is_file():
        try:
            existing = json.loads(mcp_file.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            existing = []
    if not isinstance(existing, list):
        existing = []
    merged = _merge_mcp_config(existing, conn)
    target_path.mkdir(parents=True, exist_ok=True)
    mcp_file.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest = {
        "name": name,
        "type": "mcp",
        "version": headers.get("x-capability-version") or version or "latest",
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "path": str(mcp_file),
        "enabled": bool(conn.get("enabled")),
        "required_env": required_env_keys(conn),
    }
    print(f"[install] mcp:{name} -> {mcp_file}")
    return manifest


def _install_dir_kind(
    client: MarketClient,
    name: str,
    *,
    cap_type: str,
    subdir: str,
    version: str = "",
    target: str | Path,
    dry_run: bool = False,
    skip_meta: tuple[str, ...] = (),
) -> dict[str, Any]:
    """把 zip 展开到 target/<subdir>/<name>/。"""
    target_path = Path(target).resolve()
    dest = target_path / subdir / name
    if dry_run:
        print(f"[dry-run] 将下载 {cap_type}「{name}」v{version or 'latest'} 到 {dest}")
        return {"name": name, "type": cap_type, "dry_run": True}

    content, headers = client.download(name, version, cap_type=cap_type)
    files = extract_zip(content)
    dest.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        if rel in skip_meta:
            continue
        out = dest / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)
    manifest = {
        "name": name,
        "type": cap_type,
        "version": headers.get("x-capability-version") or version or "latest",
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "path": str(dest),
    }
    (dest / "installed.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[install] {cap_type}:{name} -> {dest}")
    return manifest


def install_rule(client, name, *, version="", target, dry_run=False):
    return _install_dir_kind(
        client, name, cap_type="rule", subdir="rules", version=version, target=target, dry_run=dry_run, skip_meta=("rule.json",)
    )


def install_command(client, name, *, version="", target, dry_run=False):
    return _install_dir_kind(
        client, name, cap_type="command", subdir="commands", version=version, target=target, dry_run=dry_run, skip_meta=("command.json",)
    )


def install_hook(client, name, *, version="", target, dry_run=False):
    return _install_dir_kind(
        client, name, cap_type="hook", subdir="hooks", version=version, target=target, dry_run=dry_run, skip_meta=("hook.json",)
    )


def install_plugin(
    client: MarketClient,
    name: str,
    *,
    version: str = "",
    target: str | Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    """安装 plugin：整包解压到 target/plugins/<name>/，并尽量安装已发布子组件。"""
    target_path = Path(target).resolve()
    plugin_dir = target_path / "plugins" / name
    if dry_run:
        print(f"[dry-run] 将下载 plugin「{name}」v{version or 'latest'} 到 {plugin_dir}")
        return {"name": name, "type": "plugin", "dry_run": True}

    content, headers = client.download(name, version, cap_type="plugin")
    files = extract_zip(content)
    plugin_dir.mkdir(parents=True, exist_ok=True)
    for rel, data in files.items():
        out = plugin_dir / rel
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(data)

    # 尝试从市场详情拉取 components 并安装已发布子能力
    components: list[dict[str, Any]] = []
    try:
        item = client.find_in_catalog(name, "plugin")
        # sync 不含 components；再拉详情需能力 id。用包内 plugin.json + 目录启发式即可。
        meta_raw = files.get("plugin.json") or files.get(".cursor-plugin/plugin.json")
        if meta_raw:
            meta = json.loads(meta_raw.decode("utf-8"))
        else:
            meta = {}
        # 若目录里有 skills/ mcp.json，记录组件清单
        for path in files:
            if path.startswith("skills/") and path.endswith("/SKILL.md"):
                skill_name = path[len("skills/") : -len("/SKILL.md")]
                if skill_name and "/" not in skill_name:
                    components.append({"type": "skill", "name": skill_name})
            if path.startswith("rules/") and path.lower().endswith((".mdc", ".md", ".markdown")):
                rest = path[len("rules/") :]
                if rest and "/" not in rest:
                    stem = rest.rsplit(".", 1)[0]
                    components.append({"type": "rule", "name": stem})
            if path.startswith("commands/") and path.lower().endswith((".md", ".mdc", ".markdown", ".txt")):
                rest = path[len("commands/") :]
                if rest and "/" not in rest:
                    stem = rest.rsplit(".", 1)[0]
                    components.append({"type": "command", "name": stem})
        if "hooks/hooks.json" in files:
            components.append({"type": "hook", "name": f"{name}-hooks"})
        mcp_cfg = files.get("mcp.json")
        if mcp_cfg:
            try:
                cfg = json.loads(mcp_cfg.decode("utf-8"))
                servers = cfg.get("mcpServers") or {}
                for srv_name in servers:
                    components.append({"type": "mcp", "name": srv_name})
            except (ValueError, UnicodeDecodeError):
                pass
        _ = item, meta  # catalog presence checked via download
    except Exception:  # noqa: BLE001
        components = []

    installed_children: list[dict[str, Any]] = []
    for comp in components:
        ctype, cname = comp.get("type"), comp.get("name")
        if not ctype or not cname:
            continue
        try:
            if ctype == "skill":
                child = install_skill(
                    client, cname, target=target_path, dry_run=False
                )
            elif ctype == "mcp":
                child = install_mcp(
                    client, cname, target=target_path, dry_run=False
                )
            elif ctype == "rule":
                child = install_rule(client, cname, target=target_path, dry_run=False)
            elif ctype == "command":
                child = install_command(client, cname, target=target_path, dry_run=False)
            elif ctype == "hook":
                child = install_hook(client, cname, target=target_path, dry_run=False)
            else:
                continue
            installed_children.append(child)
        except Exception as exc:  # noqa: BLE001
            installed_children.append(
                {"name": cname, "type": ctype, "installed": False, "note": str(exc)}
            )
            print(f"  [skip] {ctype}:{cname} — {exc}")

    manifest = {
        "name": name,
        "type": "plugin",
        "version": headers.get("x-capability-version") or version or "latest",
        "installed_at": datetime.now(timezone.utc).isoformat(),
        "path": str(plugin_dir),
        "components": installed_children,
    }
    (plugin_dir / "installed.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"[install] plugin:{name} -> {plugin_dir}")
    return manifest


def install_capability(
    client: MarketClient,
    name: str,
    *,
    cap_type: str = "agent",
    version: str = "",
    target: str | Path,
    dry_run: bool = False,
    interactive: bool = True,
    env_overrides: dict[str, str] | None = None,
) -> dict[str, Any]:
    """按类型安装能力：agent / skill / mcp / plugin / rule / command / hook。"""
    t = (cap_type or "agent").lower()
    if t == "agent":
        return install_agent(
            client,
            name,
            version=version,
            target=target,
            dry_run=dry_run,
            interactive=interactive,
            env_overrides=env_overrides,
        )
    if t == "skill":
        return install_skill(client, name, version=version, target=target, dry_run=dry_run)
    if t == "mcp":
        return install_mcp(
            client,
            name,
            version=version,
            target=target,
            dry_run=dry_run,
            interactive=interactive,
            env_overrides=env_overrides,
        )
    if t == "plugin":
        return install_plugin(client, name, version=version, target=target, dry_run=dry_run)
    if t == "rule":
        return install_rule(client, name, version=version, target=target, dry_run=dry_run)
    if t == "command":
        return install_command(client, name, version=version, target=target, dry_run=dry_run)
    if t == "hook":
        return install_hook(client, name, version=version, target=target, dry_run=dry_run)
    raise ValueError(f"不支持的安装类型：{cap_type}")
