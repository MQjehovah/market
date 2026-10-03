"""agent 包读写：新格式 + 旧格式(agent.json/PROMPT.md/dependencies.json) 读侧兼容。"""

import json

from app.services.agent_package import load_prompt_deps, parse_plugin_meta


def _b(obj) -> bytes:
    return json.dumps(obj, ensure_ascii=False).encode("utf-8")


def test_new_format_plugin_json_and_agents_md():
    files = {
        "plugin.json": _b({"name": "demo", "dependencies": [{"name": "erp", "type": "mcp"}]}),
        "agents/demo.md": b"---\nname: demo\ndescription: d\n---\n\xe4\xbd\xa0\xe6\x98\xaf demo",
    }
    meta = parse_plugin_meta(files)
    assert meta["name"] == "demo"
    prompt, deps = load_prompt_deps(files, "demo")
    assert "demo" in prompt
    assert deps == [{"name": "erp", "type": "mcp"}]


def test_legacy_agent_json_prompt_and_dependencies_json():
    files = {
        "agent.json": _b({"name": "数字中台", "version": "1.1.5"}),
        "PROMPT.md": "# 你是数字中台，负责 ERP 查询".encode("utf-8"),
        "dependencies.json": _b(
            [{"name": "erp", "type": "mcp"}, {"name": "mysql_query", "type": "mcp"}]
        ),
    }
    meta = parse_plugin_meta(files)
    assert meta["name"] == "数字中台"
    prompt, deps = load_prompt_deps(files, "数字中台")
    assert "ERP" in prompt
    assert deps == [{"name": "erp", "type": "mcp"}, {"name": "mysql_query", "type": "mcp"}]


def test_legacy_deps_embedded_in_agent_json():
    files = {
        "agent.json": _b(
            {"name": "旧专家", "dependencies": [{"name": "gitlab", "type": "mcp"}]}
        ),
        "PROMPT.md": "p".encode("utf-8"),
    }
    _, deps = load_prompt_deps(files, "旧专家")
    assert deps == [{"name": "gitlab", "type": "mcp"}]
