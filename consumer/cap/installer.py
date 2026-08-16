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

        elif dep_type == "mcp":
            conn_file = dep_files.get("connection.json")
            if conn_file is not None:
                try:
                    conn = json.loads(conn_file.decode("utf-8"))
                except (ValueError, UnicodeDecodeError):
                    conn = {}
                conn.setdefault("name", dep_name)
                conn.setdefault("enabled", False)  # 连接前需人工提供凭据并显式启用
                mcp_configs = _merge_mcp_config(mcp_configs, conn)
            record["installed"] = True
            print(f"  [ok] mcp:{dep_name} -> mcp_servers.json（凭据为 ${{VAR}} 占位，需人工提供）")

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
