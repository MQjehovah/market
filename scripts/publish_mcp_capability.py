#!/usr/bin/env python3
"""把自研 MCP server（agent 仓库 mcp_server/src/*.py）打包并发布到市场能力。

用法（在服务器或本机可访问 market 的机器执行）：
    python3 publish_mcp_capability.py \
        --market http://127.0.0.1:8093 \
        --admin-password-file /home/xzrobot/market/.admin-password-20260921_211612 \
        --name gitlab --version 1.0.0 \
        --server /path/to/gitlab.py --extra /path/to/env_guard.py \
        --description "GitLab DevOps 连接器" \
        [--risk write] [--distribution both] [--verify-token-file ...]

行为：
1. 组包（zip）：connection.json(implementation 约定) + mcp.json + tools.json + security.json + README + implementation/*.py
2. 发布：admin 登录 → 建能力(type=mcp) → 上传包 → 提交 → 审核发布（已存在同版本时复用草稿）
3. 可选 --verify-token-file：用服务令牌验证 /relay/{name} initialize + tools/list
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import zipfile


def http(method: str, url: str, token: str | None = None, body: dict | None = None,
         raw: bytes | None = None, ctype: str | None = None, timeout: int = 60):
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = None
    if raw is not None:
        data = raw
        headers["Content-Type"] = ctype or "application/octet-stream"
    elif body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.loads(resp.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode() or "{}")
        except Exception:
            d = {}
        return e.code, d


def multipart(filepath: str, field: str = "file"):
    boundary = "----mcpcap" + str(int(time.time() * 1000))
    content = open(filepath, "rb").read()
    filename = os.path.basename(filepath)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{field}\"; filename=\"{filename}\"\r\n"
            f"Content-Type: application/zip\r\n\r\n").encode() + content + f"\r\n--{boundary}--\r\n".encode()
    return body, f"multipart/form-data; boundary={boundary}"


def extract_tools(server_src: str) -> list[dict]:
    """粗略提取 @mcp.tool 工具名（带注解函数名），仅用于元数据展示。"""
    names = []
    lines = server_src.splitlines()
    for i, line in enumerate(lines):
        if "@mcp.tool" in line:
            for j in range(i + 1, min(i + 4, len(lines))):
                m = re.match(r"\s*(?:async\s+)?def\s+(\w+)", lines[j])
                if m:
                    names.append({"name": m.group(1)})
                    break
    return names


def build_package(args) -> str:
    out = args.output or f"/tmp/{args.name}-{args.version}.zip"
    server_src = open(args.server, encoding="utf-8").read()
    tools = extract_tools(server_src)
    conn = {
        "name": args.name,
        "transport": "stdio",
        "command": "python",
        "args": [f"implementation/{os.path.basename(args.server)}"],
    }
    mcp_meta = {"name": args.name, "description": args.description, "version": args.version}
    sec = {"notes": args.security_notes or "凭证走 ${VAR} 占位符（在市场「我的密钥」中配置）"}
    readme = f"# {args.name}\n\n{args.description}\n\n- 传输：stdio（平台侧解压 implementation 后拉起）\n- 依赖：见实现代码 import（市场镜像预装 requests/rich/pymysql/websockets 等）\n"
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("connection.json", json.dumps(conn, ensure_ascii=False, indent=2))
        z.writestr("mcp.json", json.dumps(mcp_meta, ensure_ascii=False, indent=2))
        z.writestr("tools.json", json.dumps({"tools": tools}, ensure_ascii=False, indent=2))
        z.writestr("security.json", json.dumps(sec, ensure_ascii=False, indent=2))
        z.writestr("README.md", readme)
        z.write(args.server, f"implementation/{os.path.basename(args.server)}")
        for extra in args.extra or []:
            z.write(extra, f"implementation/{os.path.basename(extra)}")
    print(f"  已打包: {out}（工具 {len(tools)} 个，含附件 {len(args.extra or [])} 个）")
    return out


def resolve_cap_id(market: str, admin: str, name: str, version: str, payload: dict) -> str | None:
    st, d = http("POST", f"{market}/api/publish/capabilities", admin, body=payload)
    if st in (200, 201):
        return d["id"]
    st2, my = http("GET", f"{market}/api/publish/my", admin)
    if st2 == 200:
        for c in my:
            if c.get("name") == name and c.get("version") == version:
                print(f"  复用已存在草稿/版本: {c.get('status')}")
                return c["id"]
    print(f"  创建失败: {st} {str(d)[:200]}")
    return None


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--market", default="http://127.0.0.1:8093")
    ap.add_argument("--admin-password-file", required=True)
    ap.add_argument("--admin-user", default="admin")
    ap.add_argument("--name", required=True)
    ap.add_argument("--version", default="1.0.0")
    ap.add_argument("--server", required=True, help="server 入口 .py 路径")
    ap.add_argument("--extra", action="append", help="随包附件（如 env_guard.py），可多次")
    ap.add_argument("--description", default="")
    ap.add_argument("--risk", default="write", choices=["read", "write", "destructive"])
    ap.add_argument("--distribution", default="both", choices=["local", "remote", "both"])
    ap.add_argument("--security-notes", default="")
    ap.add_argument("--output", default="")
    ap.add_argument("--verify-token-file", default="")
    ap.add_argument("--skip-approve", action="store_true")
    args = ap.parse_args()

    pack = build_package(args)

    pw = open(args.admin_password_file, encoding="utf-8").read().strip()
    st, d = http("POST", f"{args.market}/api/auth/login", body={"username": args.admin_user, "password": pw})
    if st != 200:
        print(f"  登录失败: {st}"); return 1
    admin = d["access_token"]

    payload = {
        "name": args.name, "type": "mcp", "version": args.version,
        "description": args.description, "distribution": args.distribution,
        "risk_default": args.risk, "visibility": "internal", "install_policy": "optional",
    }
    cap_id = resolve_cap_id(args.market, admin, args.name, args.version, payload)
    if not cap_id:
        return 1

    raw, ctype = multipart(pack)
    st, d = http("POST", f"{args.market}/api/publish/capabilities/{cap_id}/artifact", admin, raw=raw, ctype=ctype)
    vr = d.get("validation_report") or {}
    print(f"  包上传: {st} validation ok={vr.get('ok')} errors={vr.get('errors')}")
    if st != 200:
        return 1

    http("POST", f"{args.market}/api/publish/capabilities/{cap_id}/submit", admin)
    if not args.skip_approve:
        st, d = http("POST", f"{args.market}/api/admin/capabilities/{cap_id}/review", admin,
                     body={"action": "approve", "comment": "publish_mcp_capability"})
        print(f"  审核: {st} {d.get('status', str(d)[:120])}")
        if not (isinstance(d, dict) and d.get("status") == "published"):
            return 1

    if args.verify_token_file:
        svc = open(args.verify_token_file, encoding="utf-8").read().strip()
        session = {}

        def rpc(msg):
            h = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream",
                 "Authorization": f"Bearer {svc}"}
            if session.get("id"):
                h["mcp-session-id"] = session["id"]
            req = urllib.request.Request(f"{args.market}/api/mcp-gateway/relay/{args.name}/stream",
                                         data=json.dumps(msg).encode(), headers=h, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=90) as resp:
                    sid = resp.headers.get("mcp-session-id")
                    if sid:
                        session["id"] = sid
                    return resp.status, resp.read().decode()
            except urllib.error.HTTPError as e:
                return e.code, e.read().decode()

        st, text = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05", "capabilities": {},
                                   "clientInfo": {"name": "publish-verify", "version": "1"}}})
        print(f"  /relay/{args.name} initialize -> {st} {text[:110]}")
        if st != 200:
            print("  （平台桥接未通：若见 mcp 版本/依赖类错误，先升级市场 mcp 或补依赖）")
            return 0
        rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
        st, text = rpc({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
        try:
            tools = [t["name"] for t in json.loads(text)["result"]["tools"]]
        except Exception:
            tools = []
        print(f"  tools/list -> {st} {len(tools)} 工具: {', '.join(tools[:8])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
