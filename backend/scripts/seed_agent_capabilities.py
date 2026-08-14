"""把 agent 的四类能力（agent/tool/skill/mcp）提取到 market 能力层。

做法：读取 agent 仓库中的现有资产，按市场包规范（见 app/services/packages.py
REQUIRED_FILES）打包为 zip，并以已发布状态写入 market 数据库与工件存储。
agent 本地资产保持不变，两者并行运行；后续 agent 可改为从 market 同步/下载。

用法（在 market/backend 目录下执行）：
    python scripts/seed_agent_capabilities.py --agent-root ../../agent --dry-run
    python scripts/seed_agent_capabilities.py --agent-root ../../agent
    python scripts/seed_agent_capabilities.py --agent-root ../../agent --types tool skill
"""

import argparse
import ast
import asyncio
import io
import json
import sys
import zipfile
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
BACKEND_DIR = SCRIPT_DIR.parent
sys.path.insert(0, str(BACKEND_DIR))

DEFAULT_VERSION = "1.0.0"

SKIP_PARTS = {"__pycache__", ".git", ".pytest_cache", ".ruff_cache"}
SKIP_FILES = {"data.db", "data.db-shm", "data.db-wal"}
SECRET_KEYWORDS = ("password", "secret", "token", "api_key", "apikey", "credential")


def _should_skip(rel: str) -> bool:
    parts = rel.split("/")
    return any(p in SKIP_PARTS for p in parts) or parts[-1] in SKIP_FILES


def _is_secret_key(key: str) -> bool:
    lk = key.lower()
    return any(s in lk for s in SECRET_KEYWORDS)


def _redact_env(env: dict | None) -> dict:
    """能力包内的凭据一律脱敏，避免密钥进入市场仓库/数据库。"""
    out = {}
    for k, v in (env or {}).items():
        out[k] = "${" + k + "}" if _is_secret_key(k) else v
    return out


def _frontmatter(text: str) -> dict:
    if not text.startswith("---"):
        return {}
    end = text.find("\n---", 3)
    if end <= 0:
        return {}
    meta = {}
    for line in text[3:end].strip().splitlines():
        if ":" in line:
            key, _, val = line.partition(":")
            meta[key.strip()] = val.strip().strip('"').strip("'")
    return meta


def _zip_bytes(files: dict[str, bytes]) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in files.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _collect_dir(root: Path, prefix: str) -> dict[str, bytes]:
    out = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        rel = p.relative_to(root).as_posix()
        if _should_skip(rel):
            continue
        out[f"{prefix}/{rel}"] = p.read_bytes()
    return out


def _json_bytes(data: dict, indent: int = 2) -> bytes:
    return json.dumps(data, ensure_ascii=False, indent=indent).encode("utf-8")


# ---------- tool ----------


def _extract_tool_module(path: Path) -> list[tuple[str, str, str, dict]]:
    """AST 提取工具类：(class_name, name, description, parameters)。"""
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except SyntaxError:
        return []
    results = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ClassDef):
            continue
        bases = {b.id for b in node.bases if isinstance(b, ast.Name)}
        if "BuiltinTool" not in bases:
            continue
        name = _prop_str(node, "name")
        if not name:
            continue
        results.append((node.name, name, _prop_str(node, "description"), _prop_dict(node, "parameters")))
    return results


def _prop_str(cls: ast.ClassDef, prop: str) -> str:
    for stmt in cls.body:
        if not isinstance(stmt, ast.FunctionDef) or stmt.name != prop:
            continue
        for s in stmt.body:
            if not isinstance(s, ast.Return):
                continue
            if isinstance(s.value, ast.Constant) and isinstance(s.value.value, str):
                return s.value.value
            if isinstance(s.value, ast.JoinedStr):
                parts = [
                    v.value
                    for v in s.value.values
                    if isinstance(v, ast.Constant) and isinstance(v.value, str)
                ]
                if parts:
                    return "".join(parts)
    return ""


def _prop_dict(cls: ast.ClassDef, prop: str) -> dict:
    for stmt in cls.body:
        if not isinstance(stmt, ast.FunctionDef) or stmt.name != prop:
            continue
        for s in stmt.body:
            if isinstance(s, ast.Return) and isinstance(s.value, ast.Dict):
                try:
                    value = ast.literal_eval(s.value)
                    return value if isinstance(value, dict) else {}
                except (ValueError, SyntaxError):
                    return {}
    return {}


def _build_tools(agent_root: Path, version: str) -> list[dict]:
    tools_dir = agent_root / "src" / "tools"
    packages = []
    for py in sorted(tools_dir.glob("*.py")):
        if py.name in ("__init__.py",):
            continue
        for _cls_name, name, desc, params in _extract_tool_module(py):
            schema = (
                params
                if isinstance(params, dict) and params.get("type") == "object"
                else {"type": "object", "properties": params or {}, "description": desc[:200]}
            )
            files = {
                "tool.json": _json_bytes(
                    {
                        "name": name,
                        "description": desc,
                        "version": version,
                        "category": "内置工具",
                        "tags": ["agent", "内置"],
                    }
                ),
                "schema.json": _json_bytes(schema),
                "implementation/tool.py": py.read_bytes(),
                "security.json": _json_bytes(
                    {
                        "sandbox": False,
                        "note": "由 agent 本地运行时加载；远程执行时请走市场沙箱。",
                    }
                ),
            }
            packages.append(
                {
                    "name": name,
                    "type": "tool",
                    "version": version,
                    "category": "内置工具",
                    "description": desc,
                    "tags": ["agent", "内置"],
                    "schema": schema,
                    "zip": _zip_bytes(files),
                }
            )
    return packages


# ---------- skill ----------


def _build_skills(agent_root: Path, version: str) -> list[dict]:
    skills_root = agent_root / "config" / "skills"
    packages = []
    if not skills_root.is_dir():
        return packages
    for d in sorted(skills_root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        skill_md = d / "SKILL.md"
        if not skill_md.is_file():
            continue
        text = skill_md.read_text(encoding="utf-8")
        meta = _frontmatter(text)
        name = meta.get("name") or d.name
        desc = meta.get("description") or ""
        files = {
            "skill.json": _json_bytes(
                {
                    "name": name,
                    "description": desc,
                    "version": version,
                    "category": "通用技能",
                    "tags": ["agent", "技能"],
                }
            ),
            "SKILL.md": text.encode("utf-8"),
        }
        files.update(_collect_dir(d / "references", "references"))
        files.update(_collect_dir(d / "scripts", "scripts"))
        files.update(_collect_dir(d / "assets", "assets"))
        packages.append(
            {
                "name": name,
                "type": "skill",
                "version": version,
                "category": "通用技能",
                "description": desc,
                "tags": ["agent", "技能"],
                "zip": _zip_bytes(files),
            }
        )
    return packages


# ---------- mcp ----------


def _build_mcps(agent_root: Path, version: str) -> list[dict]:
    cfg_file = agent_root / "config" / "mcp_servers.json"
    if not cfg_file.is_file():
        return []
    cfg = json.loads(cfg_file.read_text(encoding="utf-8"))
    src_root = agent_root / "mcp_server" / "src"
    packages = []
    for entry in cfg:
        name = entry.get("name")
        if not name:
            continue
        raw_env = entry.get("env") or {}
        env = _redact_env(raw_env)
        desc = entry.get("description") or ""
        files = {
            "mcp.json": _json_bytes(
                {
                    "name": name,
                    "description": desc,
                    "version": version,
                    "transport": "stdio",
                    "command": entry.get("command", "python"),
                    "args": entry.get("args", []),
                    "env": env,
                    "enabled": bool(entry.get("enabled", False)),
                }
            ),
            "connection.json": _json_bytes(
                {
                    "transport": "stdio",
                    "command": entry.get("command", "python"),
                    "args": entry.get("args", []),
                    "env": env,
                }
            ),
            "tools.json": _json_bytes({"tools": []}),
            "security.json": _json_bytes(
                {
                    "sandbox": False,
                    "redacted": [k for k in raw_env if _is_secret_key(k)],
                    "note": "凭据已脱敏，需在部署环境变量中提供。",
                }
            ),
        }
        server_src = src_root / f"{name}.py"
        if server_src.is_file():
            files[f"implementation/{name}.py"] = server_src.read_bytes()
        packages.append(
            {
                "name": name,
                "type": "mcp",
                "version": version,
                "category": "MCP 服务",
                "description": desc,
                "tags": ["agent", "MCP"],
                "zip": _zip_bytes(files),
            }
        )
    return packages


# ---------- agent ----------


def _build_agents(agent_root: Path, version: str) -> list[dict]:
    agents_root = agent_root / "config" / "agents"
    packages = []
    if not agents_root.is_dir():
        return packages
    for d in sorted(agents_root.iterdir()):
        if not d.is_dir() or d.name.startswith("."):
            continue
        prompt = d / "PROMPT.md"
        if not prompt.is_file():
            continue
        text = prompt.read_text(encoding="utf-8")
        meta = _frontmatter(text)
        name = d.name
        desc = meta.get("description") or ""
        is_team = (d / "TEAM.md").is_file() and (d / "agents").is_dir()
        files = {
            "agent.json": _json_bytes(
                {
                    "name": name,
                    "description": desc,
                    "version": version,
                    "role": name,
                }
            ),
            "PROMPT.md": text.encode("utf-8"),
        }
        team = d / "TEAM.md"
        if team.is_file():
            files["TEAM.md"] = team.read_bytes()
        files.update(_collect_dir(d / "skills", "skills"))
        files.update(_collect_dir(d / "agents", "agents"))
        packages.append(
            {
                "name": name,
                "type": "agent",
                "version": version,
                "category": "团队" if is_team else "垂直领域",
                "description": desc,
                "tags": ["agent", "persona"],
                "zip": _zip_bytes(files),
            }
        )
    return packages


# ---------- 发布 ----------


def _build_example_tool(version: str) -> list[dict]:
    """符合沙箱契约（run(params) -> dict）的示例工具，供工作流演示。"""
    name = "示例工具：文本处理"
    impl = (
        "def run(params):\n"
        "    text = str(params.get('text', ''))\n"
        "    mode = str(params.get('mode', 'echo'))\n"
        "    if mode == 'upper':\n"
        "        text = text.upper()\n"
        "    return {'ok': True, 'output': text}\n"
    )
    schema = {
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "输入文本"},
            "mode": {"type": "string", "enum": ["echo", "upper"], "description": "处理方式"},
        },
        "required": ["text"],
    }
    files = {
        "tool.json": _json_bytes(
            {
                "name": name,
                "description": "文本回显/转大写，符合沙箱 run(params) 契约的示例工具。",
                "version": version,
                "category": "示例",
                "tags": ["示例"],
            }
        ),
        "schema.json": _json_bytes(schema),
        "implementation/tool.py": impl.encode("utf-8"),
        "security.json": _json_bytes({"sandbox": True, "note": "无网络/进程操作，可沙箱执行。"}),
    }
    return [
        {
            "name": name,
            "type": "tool",
            "version": version,
            "category": "示例",
            "description": "文本回显/转大写，符合沙箱 run(params) 契约的示例工具。",
            "tags": ["示例"],
            "schema": schema,
            "zip": _zip_bytes(files),
        }
    ]


def _build_workflow_example(version: str) -> list[dict]:
    """示例工作流：文本处理工具 → 代码审查 Agent。"""
    workflow = {
        "name": "示例工作流：文本处理与代码审查",
        "description": "先用文本处理工具生成待审内容，再交给代码审查 Agent 输出问题清单。",
        "version": version,
        "on_error": "fail",
        "nodes": [
            {
                "id": "echo",
                "type": "tool",
                "capability": "示例工具：文本处理",
                "params": {"text": "${input.text}", "mode": "${input.mode}"},
            },
            {
                "id": "review",
                "type": "agent",
                "capability": "代码审查",
                "params": {
                    "task": "请审查以下内容，输出问题清单与修改建议：\n${echo.output}"
                },
            },
        ],
        "edges": [{"from": "echo", "to": "review"}],
    }
    files = {
        "workflow.json": json.dumps(workflow, ensure_ascii=False, indent=2).encode("utf-8"),
        "README.md": "示例工作流：glob 扫描 → 代码审查 Agent。".encode("utf-8"),
    }
    return [
        {
            "name": workflow["name"],
            "type": "workflow",
            "version": version,
            "category": "工作流示例",
            "description": workflow["description"],
            "tags": ["工作流", "示例"],
            "zip": _zip_bytes(files),
        }
    ]


async def _publish(
    db_url: str,
    packages: list[dict],
    author_username: str,
    force: bool,
    examples: bool = True,
) -> None:
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    from app.auth import hash_password
    from app.config import get_settings
    from app.database import Base
    from app.models import Capability, CapabilityArtifact, User
    from app.storage import get_storage

    engine = create_async_engine(db_url)
    Session = async_sessionmaker(engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    storage = get_storage()
    async with Session() as db:
        author = await db.scalar(select(User).where(User.username == author_username))
        if author is None:
            settings = get_settings()
            author = User(
                username=author_username,
                email=f"{author_username}@example.com",
                password_hash=hash_password(settings.seed_admin_password),
                display_name="能力层发布者",
                role="admin",
                organization="平台部",
            )
            db.add(author)
            await db.flush()

        created, updated, skipped = 0, 0, 0
        for pkg in packages:
            existing = await db.scalar(
                select(Capability).where(
                    Capability.name == pkg["name"],
                    Capability.type == pkg["type"],
                    Capability.version == pkg["version"],
                )
            )
            if existing is not None and not force:
                skipped += 1
                print(f"  [skip] {pkg['type']}:{pkg['name']} v{pkg['version']} 已存在")
                continue

            if existing is None:
                cap = Capability(
                    name=pkg["name"],
                    description=pkg["description"],
                    type=pkg["type"],
                    version=pkg["version"],
                    category=pkg["category"],
                    tags=pkg["tags"],
                    visibility="internal",
                    status="published",
                    author_id=author.id,
                    organization=author.organization,
                    input_schema=pkg.get("schema") or {},
                )
                db.add(cap)
                await db.flush()
                created += 1
                tag = "create"
            else:
                cap = existing
                cap.description = pkg["description"]
                cap.category = pkg["category"]
                cap.tags = pkg["tags"]
                cap.status = "published"
                cap.visibility = "internal"
                if pkg.get("schema"):
                    cap.input_schema = pkg["schema"]
                updated += 1
                tag = "update"

            info = storage.save(
                cap.id,
                f"{cap.name}-{cap.version}.zip",
                io.BytesIO(pkg["zip"]),
            )
            db.add(
                CapabilityArtifact(
                    capability_id=cap.id,
                    filename=f"{cap.name}-{cap.version}.zip",
                    **info,
                )
            )
            print(
                f"  [{tag}] {pkg['type']}:{pkg['name']} v{pkg['version']} "
                f"({len(pkg['zip'])} bytes, {info['checksum'][:12]}…)"
            )
        await db.commit()
        print(f"完成：新建 {created}，更新 {updated}，跳过 {skipped}")

        if examples:
            from app.schemas import AssembleDependency, AssembleRequest
            from app.services.assemble import assemble_agent

            req = AssembleRequest(
                persona="数字中台",
                name="数字中台-组装版",
                description=(
                    "由 数字中台 人设 + glob/grep/web_search 工具 + report-writer 技能 "
                    "+ dingtalk MCP 组装而成（示例）。"
                ),
                version="0.1.0",
                category="组装",
                tags=["组装", "示例"],
                dependencies=[
                    AssembleDependency(name="glob", type="tool"),
                    AssembleDependency(name="grep", type="tool"),
                    AssembleDependency(name="web_search", type="tool"),
                    AssembleDependency(name="report-writer", type="skill"),
                    AssembleDependency(name="dingtalk", type="mcp"),
                ],
            )
            try:
                cap = await assemble_agent(db, author, req)
                cap.status = "published"
                await db.commit()
                print(
                    f"  [assemble] agent:{cap.name} v{cap.version} 已发布"
                    f"（{len(req.dependencies)} 个依赖）"
                )
            except Exception as exc:  # noqa: BLE001
                print(f"  [assemble] 跳过组装示例：{exc}")
    await engine.dispose()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="把 agent 能力提取发布到 market 能力层")
    parser.add_argument(
        "--agent-root",
        default=str(Path(__file__).resolve().parent.parent.parent / "agent"),
        help="agent 仓库根目录（默认取 workspace 下的 agent）",
    )
    parser.add_argument("--version", default=DEFAULT_VERSION, help="能力包版本（默认 1.0.0）")
    parser.add_argument("--user", default="admin", help="作者用户名（默认 admin，不存在则创建）")
    parser.add_argument(
        "--types",
        nargs="+",
        choices=["agent", "tool", "skill", "mcp", "workflow"],
        default=["agent", "tool", "skill", "mcp", "workflow"],
    )
    parser.add_argument("--no-examples", action="store_true", help="不生成示例工作流/组装示例")
    parser.add_argument("--db-url", default="", help="覆盖 market 数据库 URL")
    parser.add_argument("--artifact-dir", default="", help="覆盖能力包存储目录")
    parser.add_argument("--force", action="store_true", help="已存在时覆盖并更新工件")
    parser.add_argument("--dry-run", action="store_true", help="只打印打包计划，不写数据库")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    if args.db_url:
        import os

        os.environ["DATABASE_URL"] = args.db_url
    if args.artifact_dir:
        import os

        os.environ["ARTIFACT_DIR"] = args.artifact_dir

    agent_root = Path(args.agent_root).resolve()
    if not (agent_root / "config" / "agents").is_dir():
        print(f"错误：{agent_root} 不是有效的 agent 仓库根目录")
        return 1

    builders = {
        "tool": lambda: _build_tools(agent_root, args.version)
        + ([] if args.no_examples else _build_example_tool(args.version)),
        "skill": lambda: _build_skills(agent_root, args.version),
        "mcp": lambda: _build_mcps(agent_root, args.version),
        "agent": lambda: _build_agents(agent_root, args.version),
        "workflow": lambda: [] if args.no_examples else _build_workflow_example(args.version),
    }
    packages: list[dict] = []
    for t in args.types:
        packages.extend(builders[t]())

    print(f"能力提取计划（agent={agent_root}，版本={args.version}）：")
    for pkg in packages:
        print(
            f"  {pkg['type']:>5}  {pkg['name']:<24} v{pkg['version']}  "
            f"{pkg['category']}  zip={len(pkg['zip'])}B"
        )
    print(f"合计 {len(packages)} 个能力包")

    if args.dry_run:
        print("dry-run：未写入数据库。")
        return 0
    if not packages:
        print("没有可发布的能力包。")
        return 0

    from app.config import get_settings

    db_url = args.db_url or get_settings().resolved_database_url()
    print(f"发布到 {db_url}，作者={args.user} ...")
    asyncio.run(
        _publish(db_url, packages, args.user, args.force, examples=not args.no_examples)
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
