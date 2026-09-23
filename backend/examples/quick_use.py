"""最小接入示例：你的 Agent/程序如何直接消费市场能力（无需 MCP，纯 HTTP）。

用法：
    python examples/quick_use.py
更推荐的方式是 MCP 桥接（见 marketplace_mcp/server.py），
这样各类 MCP 客户端 / 自研 Agent 都能原生调用。
"""

import os
import sys

import httpx

BASE = os.environ.get("MARKETPLACE_URL", "http://127.0.0.1:8000").rstrip("/")
TOKEN = (
    os.environ.get("MARKETPLACE_SSO_TOKEN", "").strip()
    or os.environ.get("MARKETPLACE_TOKEN", "").strip()
)
HEADERS = {"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}


def search(q: str = "", type_: str = "") -> list[dict]:
    params = {k: v for k, v in {"q": q, "type": type_}.items() if v}
    r = httpx.get(f"{BASE}/api/capabilities", params=params, headers=HEADERS, timeout=15)
    r.raise_for_status()
    return r.json()["items"]


def use_tool(name: str, params: dict) -> dict:
    from urllib.parse import quote

    r = httpx.post(
        f"{BASE}/api/runtime/tools/{quote(name)}/invoke",
        json={"params": params},
        headers=HEADERS,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["result"]


def run_agent(name: str, task: str) -> dict:
    from urllib.parse import quote

    r = httpx.post(
        f"{BASE}/api/runtime/agents/{quote(name)}/instances",
        params={"task": task},
        headers=HEADERS,
        timeout=60,
    )
    r.raise_for_status()
    return r.json()["result"]


def activate_skill(name: str, context: str = "") -> dict:
    from urllib.parse import quote

    r = httpx.post(
        f"{BASE}/api/runtime/skills/{quote(name)}/activate",
        json={"context": context},
        headers=HEADERS,
        timeout=30,
    )
    r.raise_for_status()
    return r.json()["result"]


def main() -> int:
    if not TOKEN:
        print(
            "提示：未设置 MARKETPLACE_TOKEN / MARKETPLACE_SSO_TOKEN，"
            "仅公开能力可访问。登录后 export MARKETPLACE_TOKEN=..."
        )

    print("== 1. 搜索能力 ==")
    for cap in search("数字")[:3]:
        print(f"  [{cap['type']}] {cap['name']} v{cap['version']} - {cap['description'][:40]}")

    print("\n== 2. 调用工具 ==")
    print("  ", use_tool("文件哈希计算", {"path": "/tmp/a.txt"}))

    print("\n== 3. 委派 Agent ==")
    print("  ", run_agent("数字中台分析师", "生成上月销售报表")["status"])

    print("\n== 4. 激活技能（返回 SKILL.md 正文）==")
    sk = activate_skill("TDD 开发工作流", "写测试")
    print("  activated=", sk.get("activated"), "skill_md_chars=", len(sk.get("skill_md") or ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
