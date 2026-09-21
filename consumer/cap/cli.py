"""能力层消费者命令行：sync / pull / install / run / serve / delegate。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from . import __version__
from .client import MarketClient
from .installer import install_capability
from .local_runner import find_agent_root, run_local
from .server import serve


def _print_json(data: Any) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2))


def _client(args) -> MarketClient:
    return MarketClient(base_url=args.url or "", token=args.token or "")


def _common_args(p: argparse.ArgumentParser) -> None:
    p.add_argument("--url", default="", help=f"能力层地址（默认 {MarketClient().base_url}）")
    p.add_argument("--token", default="", help="访问令牌（或设置 CAP_TOKEN）")
    p.add_argument("--user", default="", help="用户名（未提供 token 时登录）")
    p.add_argument("--password", default="", help="密码（未提供 token 时登录）")


def cmd_sync(args) -> int:
    client = _client(args)
    items = client.sync()
    if args.json:
        _print_json(items)
    else:
        by_type: dict[str, list[dict]] = {}
        for item in items:
            by_type.setdefault(item["type"], []).append(item)
        for cap_type in ("agent", "tool", "skill", "mcp", "workflow", "plugin"):
            group = by_type.get(cap_type)
            if not group:
                continue
            print(f"== {cap_type}（{len(group)}）==")
            for item in group:
                print(
                    f"  {item['name']}  v{item['version']}  "
                    f"{item['description'][:50]}"
                )
    return 0


def cmd_pull(args) -> int:
    client = _client(args)
    content, headers = client.download(args.name, args.version, cap_type=args.type or "")
    out = Path(args.output) if args.output else Path(f"{args.name}-{args.version or 'latest'}.zip")
    out.write_bytes(content)
    print(f"已下载能力包：{out}（{len(content)} 字节，SHA-256={headers.get('x-capability-checksum', '')}）")
    return 0


def cmd_install(args) -> int:
    client = _client(args)
    if args.token or args.user:
        client.ensure_token(args.user, args.password)
    root = find_agent_root(args.agent_root)
    target = Path(args.target) if args.target else root / "config"
    cap_type = (args.type or "agent").lower()
    manifest = install_capability(
        client,
        args.name,
        cap_type=cap_type,
        version=args.version,
        target=target,
        dry_run=args.dry_run,
    )
    if args.json:
        _print_json(manifest)
    elif not args.dry_run:
        if cap_type == "agent":
            print(f"安装清单：{target / 'agents' / args.name / 'installed.json'}")
        elif cap_type == "skill":
            print(f"安装清单：{target / 'skills' / args.name / 'installed.json'}")
        elif cap_type == "mcp":
            print(f"已合并：{target / 'mcp_servers.json'}")
        elif cap_type == "plugin":
            print(f"安装清单：{target / 'plugins' / args.name / 'installed.json'}")
    return 0


def cmd_run(args) -> int:
    client = _client(args)
    if args.mode in ("cloud", "a2a"):
        client.ensure_token(args.user, args.password)
    if args.mode == "local":
        root = find_agent_root(args.agent_root)
        config_dir = args.config_dir or str(root / "config")
        result = run_local(
            args.name,
            args.task,
            config_dir=config_dir,
            agent_root=str(root),
            workspace=args.workspace,
            timeout=args.timeout,
        )
        if args.json:
            _print_json(result)
        else:
            print(result.get("output") or result.get("error") or "（无输出）")
            if result.get("stderr_tail"):
                print(f"\n[stderr]\n{result['stderr_tail']}", file=sys.stderr)
        return 0 if result.get("ok") else 1

    if args.mode == "cloud":
        result = client.run_agent_cloud(args.name, args.task)
        if args.json:
            _print_json(result)
        else:
            print(result.get("output") or "")
        return 0

    # a2a 委派：发现云端 Agent Card → tasks/send
    if args.card_url:
        card_url = args.card_url
    else:
        card = client.find_agent_card(args.name)
        card_url = card["url"]
    resp = client.a2a_send(card_url, args.task)
    if resp.get("error"):
        print(f"A2A 错误：{resp['error']}", file=sys.stderr)
        return 1
    task = resp.get("result") or {}
    if args.json:
        _print_json(resp)
    else:
        state = task.get("status", {}).get("state")
        print(f"任务 {task.get('id')} 状态：{state}")
        artifacts = task.get("artifacts") or []
        if artifacts:
            for part in artifacts[0].get("parts") or []:
                if part.get("text"):
                    print(part["text"])
    return 0 if task.get("status", {}).get("state") == "completed" else 1


def cmd_delegate(args) -> int:
    """A2A 客户端：委派任务给指定云端 Agent（等价于 run --mode a2a）。"""
    return cmd_run(args)


def cmd_serve(args) -> int:
    root = find_agent_root(args.agent_root)
    config_dir = args.config_dir or str(root / "config")
    serve(
        host=args.host,
        port=args.port,
        config_dir=config_dir,
        agent_root=str(root),
        workspace=args.workspace,
        task_timeout=args.timeout,
    )
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cap",
        description="能力层消费者：同步 / 下载 / 组装 / 运行 / 互调能力层能力",
    )
    parser.add_argument("--version", action="version", version=f"cap {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("sync", help="同步能力平台")
    _common_args(p)
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(func=cmd_sync)

    p = sub.add_parser("pull", help="下载单个能力包")
    _common_args(p)
    p.add_argument("name", help="能力名称")
    p.add_argument("--version", "-v", default="", help="版本（默认最新）")
    p.add_argument("--type", default="", help="能力类型（agent/tool/skill/mcp/plugin，用于目录消歧）")
    p.add_argument("--output", "-o", default="", help="保存路径（默认 <name>-<version>.zip）")
    p.set_defaults(func=cmd_pull)

    p = sub.add_parser("install", help="下载并安装能力（agent/skill/mcp/plugin）")
    _common_args(p)
    p.add_argument("name", help="能力名称")
    p.add_argument(
        "--type",
        default="agent",
        choices=["agent", "skill", "mcp", "plugin"],
        help="安装类型（默认 agent）",
    )
    p.add_argument("--version", "-v", default="", help="版本（默认最新）")
    p.add_argument("--target", default="", help="目标配置目录（默认 <agent-root>/config）")
    p.add_argument("--agent-root", default="", help="本地 agent 仓库根目录（默认 AGENT_ROOT 或 E:\\ai\\agent）")
    p.add_argument("--dry-run", action="store_true", help="只打印组装计划，不写盘")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(func=cmd_install)

    p = sub.add_parser("run", help="执行 Agent 任务（本地/云端/A2A 三种模式）")
    _common_args(p)
    p.add_argument("name", help="Agent 名称")
    p.add_argument("--task", "-t", required=True, help="任务内容")
    p.add_argument(
        "--mode",
        choices=["local", "cloud", "a2a"],
        default="local",
        help="local=本地引擎；cloud=能力层云端 runtime；a2a=委派云端 Agent",
    )
    p.add_argument("--config-dir", default="", help="本地 agent 配置目录（默认 <agent-root>/config）")
    p.add_argument("--agent-root", default="", help="本地 agent 仓库根目录")
    p.add_argument("--workspace", default="", help="本地工作目录（默认 agent 仓库 workspace）")
    p.add_argument("--timeout", type=int, default=900, help="本地/A2A 超时秒数")
    p.add_argument("--card-url", default="", help="A2A 模式：直接指定 Agent Card URL")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(func=cmd_run)

    p = sub.add_parser("delegate", help="A2A 委派任务给云端 Agent（等价 run --mode a2a）")
    _common_args(p)
    p.add_argument("name", help="云端 Agent 名称")
    p.add_argument("--task", "-t", required=True, help="任务内容")
    p.add_argument("--card-url", default="", help="直接指定 Agent Card URL")
    p.add_argument("--timeout", type=int, default=900, help="超时秒数")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(func=cmd_delegate, mode="a2a", config_dir="", agent_root="", workspace="")

    p = sub.add_parser("serve", help="把本地 Agent 暴露为 A2A 服务（供云端/本地互调）")
    _common_args(p)
    p.add_argument("--host", default="127.0.0.1", help="监听地址（默认 127.0.0.1）")
    p.add_argument("--port", type=int, default=8765, help="监听端口（默认 8765）")
    p.add_argument("--config-dir", default="", help="本地 agent 配置目录")
    p.add_argument("--agent-root", default="", help="本地 agent 仓库根目录")
    p.add_argument("--workspace", default="", help="本地工作目录")
    p.add_argument("--timeout", type=int, default=900, help="任务超时秒数")
    p.set_defaults(func=cmd_serve)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except Exception as exc:  # noqa: BLE001
        print(f"错误：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
