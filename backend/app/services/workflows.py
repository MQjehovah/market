"""工作流引擎（Dify 式编排器 MVP）：DAG 并行调度 + 条件分支 + 迭代 + 变量传递。

节点类型：
    start          起始节点(声明可用输入字段)；输出 = 执行入参
    end            结束节点(声明输出映射)；作为工作流最终结果
    llm            调用市场配置的 LLM（system/prompt 模板）
    http           发起 HTTP 请求
    if_else        条件判断，向下游按 true/false 分支（边 condition）
    iteration      对数组逐项执行 body 子节点
    template       模板渲染（纯文本/JSON 变换）
    tool/agent/skill/mcp  复用市场能力（沙箱执行 / A2A 委派 / 技能激活 / MCP 下发）

变量模板：${input.key} 引用入参；${nodeId.field} 引用前序节点输出；
单值形如 ${nodeId.field} 时保留原始类型（数组/对象），否则内插为字符串。
边 condition：仅 if_else 源节点使用，取 'true'/'false'；未标注的边恒激活。
"""

import asyncio
import io
import json
import os
import re
import time
import zipfile
from typing import Any

import httpx
from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.a2a.protocol import extract_text
from app.a2a.service import send_task
from app.config import get_settings
from app.models import Capability, User, WorkflowConversation, WorkflowExecution
from app.services.marketplace import (
    activate_skill,
    install_mcp,
    invoke_tool,
    record_usage,
    resolve_capability,
)
from app.storage import get_storage

_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")
# Dify 变量语法 {{#node.field#}}：统一转成内部 ${node.field}
_DIFY_RE = re.compile(r"\{\{#([^#]+)#\}\}")
_MARKET_TYPES = {"tool", "agent", "skill", "mcp"}
_NODE_TYPES = _MARKET_TYPES | {
    "start", "end", "llm", "http", "if_else", "iteration", "template",
    "question_classifier", "parameter_extractor", "list_operator", "doc_extractor",
    "variable_aggregator", "variable_assigner", "loop", "answer", "knowledge_retrieval", "code",
    "approval",
}
# Dify 节点名(连字符) → 内部名(下划线)；对齐 Dify 同时不改内部实现
_TYPE_ALIASES = {
    "if-else": "if_else",
    "http-request": "http",
    "template-transform": "template",
    "question-classifier": "question_classifier",
    "parameter-extractor": "parameter_extractor",
    "list-operator": "list_operator",
    "doc-extractor": "doc_extractor",
    "variable-aggregator": "variable_aggregator",
    "variable-assigner": "variable_assigner",
    "knowledge-retrieval": "knowledge_retrieval",
    "human-approval": "approval",
}
_MAX_PARALLEL = 4


def _canon_type(t: Any) -> str:
    return _TYPE_ALIASES.get(str(t or ""), str(t or ""))


def load_workflow_definition(cap: Capability, require_nodes: bool = True) -> dict[str, Any]:
    if not cap.artifacts:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"工作流 {cap.name} 未上传能力包")
    content = get_storage().open(cap.artifacts[-1].uri).read()
    try:
        with zipfile.ZipFile(io.BytesIO(content)) as zf:
            raw = zf.read("workflow.json")
    except (zipfile.BadZipFile, KeyError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "工作流包缺少 workflow.json") from exc
    try:
        definition = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "workflow.json 不是合法 JSON") from exc
    validate_definition(definition, require_nodes=require_nodes)
    return definition


def _validate_nodes(nodes: list, require_capability: bool = True) -> list[str]:
    node_ids: list[str] = []
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id") or not node.get("type"):
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "每个节点必须包含 id 与 type")
        ntype = _canon_type(node["type"])
        if ntype not in _NODE_TYPES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"节点 {node['id']} 类型未知：{ntype}（可选 {sorted(_NODE_TYPES)}）",
            )
        if ntype in _MARKET_TYPES and not node.get("capability"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"节点 {node['id']}（{ntype}）缺少 capability"
            )
        # iteration 的 body 递归校验
        if ntype == "iteration":
            body = node.get("body") or (node.get("params") or {}).get("body")
            if isinstance(body, list):
                _validate_nodes(body, require_capability=require_capability)
        node_ids.append(node["id"])
    if len(set(node_ids)) != len(node_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "节点 id 不能重复")
    return node_ids


def validate_definition(definition: dict[str, Any], require_nodes: bool = True) -> list[str]:
    """校验节点/边并返回拓扑顺序；非法或存在环时抛 422。"""
    nodes = definition.get("nodes")
    if not isinstance(nodes, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "workflow.json 必须包含非空 nodes")
    if not nodes and require_nodes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "workflow.json 必须包含非空 nodes")

    node_ids = _validate_nodes(nodes)

    edges = definition.get("edges") or []
    if not isinstance(edges, list):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "edges 必须是列表")
    id_set = set(node_ids)
    adjacency: dict[str, list[str]] = {nid: [] for nid in node_ids}
    indegree: dict[str, int] = {nid: 0 for nid in node_ids}
    for edge in edges:
        src, dst = edge.get("from"), edge.get("to")
        if src not in id_set or dst not in id_set:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"边 {src} -> {dst} 引用了不存在的节点"
            )
        adjacency[src].append(dst)
        indegree[dst] += 1

    # Kahn 拓扑排序，检测环
    queue = [nid for nid, deg in indegree.items() if deg == 0]
    order: list[str] = []
    while queue:
        nid = queue.pop(0)
        order.append(nid)
        for nxt in adjacency[nid]:
            indegree[nxt] -= 1
            if indegree[nxt] == 0:
                queue.append(nxt)
    if len(order) != len(node_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "工作流存在环，无法执行")
    return order


def _resolve_path(ctx: dict[str, Any], path: str) -> Any:
    cur: Any = ctx
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return None
    return cur


def render_value(value: Any, ctx: dict[str, Any]) -> Any:
    """递归渲染 ${path} 模板；整串恰为一个 ${path} 时保留原始类型。"""
    if isinstance(value, dict):
        return {k: render_value(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, ctx) for v in value]
    if isinstance(value, str):
        if "{{#" in value:
            value = _DIFY_RE.sub(lambda m: "${" + m.group(1).strip() + "}", value)
        exact = _TEMPLATE_RE.fullmatch(value)
        if exact:
            return _resolve_path(ctx, exact.group(1).strip())
        return _TEMPLATE_RE.sub(lambda m: _stringify(_resolve_path(ctx, m.group(1).strip())), value)
    return value


def _extract_json(text: str) -> str:
    t = (text or "").strip()
    if t.startswith("```"):
        t = t.split("\n", 1)[-1].rsplit("```", 1)[0]
    return t.strip()


def _mcp_tool_prefix(name: str) -> str:
    return re.sub(r"[^0-9A-Za-z_]", "_", name)[:32] or "mcp"


def _stringify(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, (str, int, float, bool)):
        return str(v)
    return json.dumps(v, ensure_ascii=False)


def collect_answers(nodes: dict[str, Any], outputs: dict[str, Any]) -> str:
    """汇总 answer 节点输出为会话回复（按节点声明顺序）。"""
    parts: list[str] = []
    for nid, node in nodes.items():
        if _canon_type(node.get("type")) != "answer":
            continue
        out = outputs.get(nid)
        val = out.get("answer") if isinstance(out, dict) else out
        if val not in (None, ""):
            parts.append(_stringify(val))
    return "\n".join(parts)


def _eval_condition(cond: dict[str, Any], ctx: dict[str, Any]) -> bool:
    op = str(cond.get("operator") or "eq")
    left = render_value(cond.get("left"), ctx)
    right = render_value(cond.get("right"), ctx)

    def _num(v: Any) -> float | None:
        try:
            return float(v)
        except (TypeError, ValueError):
            return None

    if op == "eq":
        return str(left) == str(right)
    if op == "ne":
        return str(left) != str(right)
    if op == "contains":
        return str(right) in str(left or "")
    if op == "not_empty":
        return left not in (None, "", [], {})
    if op == "empty":
        return left in (None, "", [], {})
    if op == "regex":
        try:
            return re.search(str(right), str(left or "")) is not None
        except re.error:
            return False
    ln, rn = _num(left), _num(right)
    if ln is None or rn is None:
        return False
    if op == "gt":
        return ln > rn
    if op == "lt":
        return ln < rn
    if op == "ge":
        return ln >= rn
    if op == "le":
        return ln <= rn
    return False


async def _llm_chat(params: dict[str, Any], ctx: dict[str, Any]) -> str:
    s = get_settings()
    if not (s.llm_base_url and s.llm_api_key and s.llm_model):
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY,
            "LLM 节点未配置：请设置 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL",
        )
    system = str(render_value(params.get("system") or "", ctx) or "")
    prompt = str(render_value(params.get("prompt") or params.get("text") or "", ctx) or "")
    model = str(params.get("model") or s.llm_model)
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    payload: dict[str, Any] = {"model": model, "messages": messages}
    if isinstance(params.get("temperature"), (int, float)):
        payload["temperature"] = params["temperature"]
    if isinstance(params.get("max_tokens"), int):
        payload["max_tokens"] = params["max_tokens"]
    async with httpx.AsyncClient(timeout=300) as client:
        resp = await client.post(
            f"{s.llm_base_url.rstrip('/')}/chat/completions",
            headers={"Authorization": f"Bearer {s.llm_api_key}", "Content-Type": "application/json"},
            json=payload,
        )
    if resp.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"LLM 调用失败 {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    return (data.get("choices") or [{}])[0].get("message", {}).get("content", "") or ""


async def _http_request(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    method = str(params.get("method") or "GET").upper()
    url = str(render_value(params.get("url") or "", ctx) or "")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"http 节点 url 非法：{url!r}")
    headers = render_value(params.get("headers") or {}, ctx)
    timeout = float(params.get("timeout_seconds") or 60)
    kwargs: dict[str, Any] = {}
    if params.get("body") is not None:
        kwargs["json"] = render_value(params["body"], ctx)
    if params.get("params") is not None:
        kwargs["params"] = render_value(params["params"], ctx)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.request(method, url, headers=headers or None, **kwargs)
    try:
        body: Any = resp.json()
    except ValueError:
        body = resp.text[:100000]
    return {"status": resp.status_code, "body": body}


async def _knowledge_retrieval(params: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
    """知识库检索：经 MARKET_RAG_URL 调 RAG /api/search；未配置则返回空并提示。"""
    base = (os.environ.get("MARKET_RAG_URL") or "").rstrip("/")
    query = str(render_value(params.get("query") or (ctx.get("input") or {}).get("query") or "", ctx) or "")
    top_k = int(params.get("top_k") or 5)
    if not base:
        return {"hits": [], "query": query, "note": "未配置知识库（设置 MARKET_RAG_URL 后可检索）"}
    token = os.environ.get("MARKET_RAG_TOKEN") or ""
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    async with httpx.AsyncClient(timeout=60) as client:
        resp = await client.post(f"{base}/api/search", json={"query": query, "top_k": top_k}, headers=headers)
    if resp.status_code >= 400:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"知识库检索失败 {resp.status_code}: {resp.text[:300]}")
    data = resp.json()
    hits = data.get("results") if isinstance(data, dict) else data
    return {"hits": hits or [], "query": query}


async def _call_mcp(db: AsyncSession, user: User, cap: Capability, params: dict[str, Any]) -> dict[str, Any]:
    """调用市场 MCP 能力的工具（复用 MCPBridge 连接 + call）。"""
    from app.services.capability_secrets import resolve_capability_env
    from app.services.mcp_bridge import MCPBridge
    from app.services.mcp_gateway import load_gateway_config_by_name

    tool = str(params.get("tool") or "").strip()
    if not tool:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "mcp 节点 op=call 需要 tool")
    args = params.get("args") or {}
    if not isinstance(args, dict):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "mcp 节点 args 必须是对象")

    async def _gw(name: str):
        try:
            return await load_gateway_config_by_name(db, name)
        except Exception:  # noqa: BLE001
            return None

    try:
        env = await resolve_capability_env(db, cap.name)
    except Exception:  # noqa: BLE001
        env = {}

    bridge = MCPBridge(gateway_loader=_gw)
    try:
        info = await bridge.connect_capability(cap.name, cap, env=env)
        if not info.get("connected"):
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"MCP {cap.name} 连接失败：{info.get('error')}")
        exposed = f"mcp_{_mcp_tool_prefix(cap.name)}_{tool}"
        if not bridge.has_tool(exposed):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"MCP {cap.name} 无工具 {tool}（可用：{', '.join(bridge.tool_names)}）",
            )
        text = await bridge.call(exposed, args)
    finally:
        await bridge.close()
    try:
        parsed: Any = json.loads(text)
    except Exception:  # noqa: BLE001
        parsed = text
    return {"server": cap.name, "tool": tool, "result": parsed}


async def _execute_node(
    db: AsyncSession,
    user: User,
    node: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    """执行单个节点，返回 {output, branch?}。"""
    ntype = _canon_type(node["type"])
    params = render_value(node.get("params") or {}, ctx)

    if ntype in _MARKET_TYPES:
        cap = await resolve_capability(db, user, node["capability"], node.get("version") or None)
        if ntype == "tool":
            result = await invoke_tool(db, user, cap, params)
            if result.get("status") == "error":
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"工具 {cap.name} 执行失败：{result.get('error')}")
            return {"output": result}
        if ntype == "agent":
            task_text = str(params.get("task") or params.get("text") or "")
            task = await send_task(
                db, user, cap,
                client_task_id=f"wf-{node['id']}",
                message={"role": "user", "parts": [{"type": "text", "text": task_text}]},
                metadata={"source": "workflow", "node": node["id"]},
            )
            text = extract_text(task.output_message or {}) if task.output_message else ""
            return {"output": {"task_id": task.id, "state": task.state, "text": text, "agent": cap.name}}
        if ntype == "skill":
            return {"output": await activate_skill(db, user, cap, str(params.get("context") or ""))}
        if ntype == "mcp":
            p = node.get("params") or {}
            if str(p.get("op") or "").lower() == "call" or p.get("tool"):
                return {"output": await _call_mcp(db, user, cap, params)}
            return {"output": await install_mcp(db, user, cap, params.get("config") or {})}

    if ntype == "start":
        fields = (node.get("params") or {}).get("fields")
        data = ctx.get("input") or {}
        if isinstance(fields, list) and fields:
            return {"output": {str(k): data.get(k) for k in fields}}
        return {"output": data}
    if ntype == "end":
        mapping = (node.get("params") or {}).get("outputs") or {}
        return {"output": render_value(mapping, ctx)}
    if ntype == "llm":
        text = await _llm_chat(node.get("params") or {}, ctx)
        return {"output": {"text": text}}
    if ntype == "http":
        return {"output": await _http_request(node.get("params") or {}, ctx)}
    if ntype == "template":
        raw = (node.get("params") or {}).get("template")
        if raw is None:
            raw = (node.get("params") or {}).get("output")
        rendered = render_value(raw, ctx)
        return {"output": {"text": _stringify(rendered), "value": rendered}}
    if ntype == "if_else":
        p = node.get("params") or {}
        conds = p.get("conditions") if isinstance(p.get("conditions"), list) else []
        if not conds and (p.get("left") is not None or p.get("operator")):
            conds = [{k: p.get(k) for k in ("left", "operator", "right")}]
        results = [_eval_condition(c, ctx) for c in conds if isinstance(c, dict)]
        logic = str(p.get("logic") or "and").lower()
        result = (all(results) if logic == "and" else any(results)) if results else False
        return {"output": {"result": result}, "branch": "true" if result else "false"}
    if ntype == "iteration":
        body = node.get("body") or (node.get("params") or {}).get("body") or []
        items = render_value((node.get("params") or {}).get("items"), ctx)
        if items is None:
            items = []
        if not isinstance(items, list):
            items = [items]
        results: list[Any] = []
        for idx, item in enumerate(items):
            subctx: dict[str, Any] = {**ctx, "item": item, "index": idx}
            last: Any = None
            for bn in body if isinstance(body, list) else []:
                sub = await _execute_node(db, user, bn, subctx)
                last = sub.get("output")
                subctx[bn["id"]] = last
            results.append(last)
        return {"output": {"items": results, "count": len(results)}}
    if ntype == "question_classifier":
        p = node.get("params") or {}
        classes = p.get("classes") if isinstance(p.get("classes"), list) else []
        query = str(render_value(p.get("query") or (ctx.get("input") or {}).get("query") or "", ctx) or "")
        roster = "\n".join(
            f'- {c.get("id")}: {c.get("name", "")} — {c.get("description", "")}'
            for c in classes if isinstance(c, dict)
        )
        prompt = (
            "根据用户输入，从下列类别中选择最合适的一个。只返回 JSON {\"id\": \"<类别id>\"}。\n\n"
            f"类别：\n{roster}\n\n用户输入：\n{query}"
        )
        cid = None
        try:
            cid = json.loads(_extract_json(await _llm_chat({"prompt": prompt}, ctx))).get("id")
        except Exception:  # noqa: BLE001
            cid = None
        valid = {str(c.get("id")) for c in classes if isinstance(c, dict)}
        if str(cid) not in valid:
            cid = next(iter(valid)) if valid else "default"
        cname = next((c.get("name") for c in classes if isinstance(c, dict) and str(c.get("id")) == str(cid)), "")
        return {"output": {"class_id": cid, "class_name": cname}, "branch": str(cid)}
    if ntype == "parameter_extractor":
        p = node.get("params") or {}
        defs = p.get("parameters") if isinstance(p.get("parameters"), list) else []
        query = str(render_value(p.get("query") or (ctx.get("input") or {}).get("query") or "", ctx) or "")
        prompt = (
            "从用户输入中抽取下列参数，只返回 JSON 对象（键为参数名）。\n"
            f"参数定义：{json.dumps(defs, ensure_ascii=False)}\n用户输入：{query}"
        )
        extracted = {}
        try:
            parsed = json.loads(_extract_json(await _llm_chat({"prompt": prompt}, ctx)))
            if isinstance(parsed, dict):
                extracted = parsed
        except Exception:  # noqa: BLE001
            extracted = {}
        return {"output": {"params": extracted}}
    if ntype == "list_operator":
        p = node.get("params") or {}
        data = render_value(p.get("list"), ctx)
        if not isinstance(data, list):
            data = [] if data is None else [data]
        op = str(p.get("operation") or "head").lower()
        count = int(p.get("count") or 1)
        key = p.get("key")
        if op == "length":
            result: Any = len(data)
        elif op == "head":
            result = data[:count]
        elif op == "tail":
            result = data[-count:] if count > 0 else []
        elif op == "unique":
            uniq: list[Any] = []
            for item in data:
                if item not in uniq:
                    uniq.append(item)
            result = uniq
        elif op == "filter":
            want = str(render_value(p.get("value"), ctx))
            result = [x for x in data if isinstance(x, dict) and str(x.get(key)) == want]
        elif op == "sort":
            rev = str(p.get("order") or "asc").lower() == "desc"
            try:
                result = sorted(data, key=lambda x: (x.get(key) if isinstance(x, dict) and key else x), reverse=rev)
            except Exception:  # noqa: BLE001
                result = data
        else:
            result = data
        return {"output": {"result": result, "count": len(result) if isinstance(result, list) else 1}}
    if ntype == "doc_extractor":
        p = node.get("params") or {}
        raw = render_value(p.get("text") if p.get("text") is not None else p.get("content"), ctx)
        text = raw if isinstance(raw, str) else _stringify(raw)
        out: dict[str, Any] = {"text": text}
        try:
            out["json"] = json.loads(text)
        except Exception:  # noqa: BLE001
            pass
        return {"output": out}
    if ntype == "variable_aggregator":
        p = node.get("params") or {}
        var_list = p.get("variables") if isinstance(p.get("variables"), list) else []
        vals = [render_value(v, ctx) for v in var_list]
        mode = str(p.get("mode") or "first").lower()
        if mode == "append":
            agg: Any = vals
        elif mode == "concat":
            agg = "".join(_stringify(x) for x in vals if x not in (None, ""))
        else:
            agg = next((x for x in vals if x not in (None, "", [], {})), (vals[0] if vals else None))
        return {"output": {"result": agg}}
    if ntype == "variable_assigner":
        p = node.get("params") or {}
        assigns = p.get("assignments") if isinstance(p.get("assignments"), dict) else {}
        rendered = {k: render_value(v, ctx) for k, v in assigns.items()}
        conv = ctx.setdefault("conversation", {})
        if isinstance(conv, dict):
            conv.update(rendered)
        return {"output": {"assigned": rendered}}
    if ntype == "loop":
        p = node.get("params") or {}
        body = node.get("body") or p.get("body") or []
        max_it = int(p.get("max_iterations") or 10)
        times = p.get("times")
        while_cond = p.get("while")
        loop_results: list[Any] = []
        for i in range(max_it):
            if isinstance(times, int) and i >= times:
                break
            if isinstance(while_cond, dict) and not _eval_condition(while_cond, {**ctx, "results": loop_results}):
                break
            subctx: dict[str, Any] = {**ctx, "index": i, "results": loop_results}
            last: Any = None
            for bn in body if isinstance(body, list) else []:
                sub = await _execute_node(db, user, bn, subctx)
                last = sub.get("output")
                subctx[bn["id"]] = last
            loop_results.append(last)
        return {"output": {"results": loop_results, "count": len(loop_results)}}
    if ntype == "answer":
        p = node.get("params") or {}
        val = render_value(p.get("answer") if p.get("answer") is not None else p.get("template"), ctx)
        return {"output": {"answer": val}}
    if ntype == "knowledge_retrieval":
        return {"output": await _knowledge_retrieval(node.get("params") or {}, ctx)}
    if ntype == "code":
        from app.services.sandbox import run_code

        p = node.get("params") or {}
        code = str(p.get("code") or "")
        inputs = render_value(p.get("inputs") or {}, ctx)
        if not isinstance(inputs, dict):
            inputs = {"inputs": inputs}
        try:
            res = await run_code(
                str(p.get("language") or "python"), code, inputs, int(p.get("timeout_seconds") or 30)
            )
        except Exception as exc:  # noqa: BLE001
            raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"code 执行失败: {exc}")
        return {"output": res}
    if ntype == "approval":
        p = node.get("params") or {}
        return {
            "wait": {
                "node_id": node["id"],
                "title": str(render_value(p.get("title") or "待审批", ctx) or "待审批"),
                "description": str(render_value(p.get("description") or "", ctx) or ""),
                "assignee": str(p.get("assignee") or ""),
                "timeout_seconds": int(p.get("timeout_seconds") or 0),
            }
        }

    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"未知节点类型 {ntype}")


async def _load_conversation_history(
    db: AsyncSession, conversation: WorkflowConversation, limit: int
) -> list[dict[str, str]]:
    """按会话载入最近 history（user 提问 + assistant 回复），从旧到新。"""
    if limit <= 0:
        return []
    rows = list(
        (
            await db.scalars(
                select(WorkflowExecution)
                .where(
                    WorkflowExecution.conversation_id == conversation.id,
                    WorkflowExecution.workflow_id == conversation.workflow_id,
                )
                .order_by(WorkflowExecution.created_at.desc())
                .limit(limit)
            )
        ).all()
    )
    turns: list[dict[str, str]] = []
    for ex in reversed(rows):
        query = ""
        if isinstance(ex.input_data, dict):
            query = str(ex.input_data.get("query") or "")
        answer = ""
        if isinstance(ex.outputs, dict):
            answer = str(ex.outputs.get("_answer") or "")
        if query:
            turns.append({"role": "user", "content": query})
        if answer:
            turns.append({"role": "assistant", "content": answer})
    return turns


def _index_edges(
    nodes: dict[str, Any], edges: list[dict[str, Any]]
) -> tuple[dict[str, list[int]], dict[str, list[int]]]:
    incoming: dict[str, list[int]] = {nid: [] for nid in nodes}
    outgoing: dict[str, list[int]] = {nid: [] for nid in nodes}
    for i, e in enumerate(edges):
        incoming[e["to"]].append(i)
        outgoing[e["from"]].append(i)
    return incoming, outgoing


def _build_ctx(
    input_data: dict[str, Any],
    history: list[dict[str, str]],
    conv_vars: dict[str, Any],
    conversation_id: str = "",
) -> dict[str, Any]:
    return {
        "input": input_data,
        "sys": {
            "query": input_data.get("query") if isinstance(input_data, dict) else "",
            "now": time.strftime("%Y-%m-%d %H:%M:%S"),
            "history": history,
            "conversation_id": conversation_id,
        },
        "history": history,
        "conversation": dict(conv_vars or {}),
    }


async def _run_schedule(
    db: AsyncSession,
    user: User,
    nodes: dict[str, Any],
    edges: list[dict[str, Any]],
    incoming: dict[str, list[int]],
    outgoing: dict[str, list[int]],
    ctx: dict[str, Any],
    state: dict[str, Any],
    execution: WorkflowExecution,
    overall_timeout: int = 0,
    on_error: str = "fail",
) -> None:
    """推进 DAG 直到完成 / 失败 / 出现审批等待；就地更新 state。

    出现审批等待时把对应节点置 'waiting' 并记录 pending、execution.state='waiting'，
    之后可经 resume_workflow 续跑。
    """
    node_states = state["node_states"]
    edge_active = state["edge_active"]
    outputs = state["outputs"]
    pending: list[dict[str, Any]] = state.setdefault("pending", [])

    def _skip_downstream() -> None:
        for nid, st in node_states.items():
            if st == "pending":
                node_states[nid] = "skipped"

    async def _run_one(nid: str) -> dict[str, Any]:
        node = nodes[nid]
        node_timeout = int(node.get("timeout_seconds") or 120)
        retries = int(node.get("retries") or 0)
        attempt = 0
        while True:
            try:
                result = await asyncio.wait_for(
                    _execute_node(db, user, node, ctx), timeout=node_timeout
                )
                break
            except asyncio.TimeoutError:
                node_states[nid] = "timeout"
                if attempt >= retries:
                    raise HTTPException(
                        status.HTTP_504_GATEWAY_TIMEOUT,
                        f"节点 {nid} 执行超时（>{node_timeout}s）",
                    )
            except HTTPException:
                node_states[nid] = "failed"
                if attempt >= retries:
                    raise
            attempt += 1
        if isinstance(result, dict) and result.get("wait"):
            node_states[nid] = "waiting"
            await db.commit()
            return {"nid": nid, "wait": result["wait"], "exc": None}
        node_output = result.get("output") or {}
        outputs[nid] = node_output
        ctx[nid] = node_output
        node_states[nid] = "succeeded"
        if _canon_type(node["type"]) in ("if_else", "question_classifier"):
            branch = str(
                result.get("branch")
                or ("true" if _canon_type(node["type"]) == "if_else" else "")
            ).strip().lower()
            for ei in outgoing[nid]:
                cond = str(edges[ei].get("condition") or "").strip().lower()
                edge_active[ei] = (cond == "") or (cond == branch)
        await db.commit()
        return {"nid": nid, "wait": None, "exc": None}

    try:
        while True:
            if execution.state == "canceled":
                break
            if overall_timeout:
                elapsed = (
                    execution.updated_at.astimezone() - execution.created_at.astimezone()
                ).total_seconds()
                if elapsed >= overall_timeout:
                    execution.state = "failed"
                    execution.error = f"工作流超时（>{overall_timeout}s）"
                    _skip_downstream()
                    break

            progressed = False
            ready: list[str] = []
            for nid, st in node_states.items():
                if st != "pending":
                    continue
                inc = incoming[nid]
                active_inc = [ei for ei in inc if edge_active[ei]]
                if inc and not active_inc:
                    node_states[nid] = "skipped"
                    for ei in outgoing[nid]:
                        edge_active[ei] = False
                    progressed = True
                    continue
                srcs = [edges[ei]["from"] for ei in active_inc]
                if any(node_states[s] in ("failed", "skipped", "timeout") for s in srcs):
                    node_states[nid] = "skipped"
                    for ei in outgoing[nid]:
                        edge_active[ei] = False
                    progressed = True
                    continue
                if all(node_states[s] == "succeeded" for s in srcs):
                    ready.append(nid)
            if not ready:
                if progressed:
                    await db.commit()
                    continue
                break

            for nid in ready:
                node_states[nid] = "running"
            await db.commit()

            sem = asyncio.Semaphore(_MAX_PARALLEL)

            async def _guarded(nid: str) -> dict[str, Any]:
                async with sem:
                    try:
                        return await _run_one(nid)
                    except Exception as exc:  # noqa: BLE001
                        return {"nid": nid, "wait": None, "exc": exc}

            results = await asyncio.gather(*[_guarded(nid) for nid in ready])
            fatal = False
            for res in results:
                nid = res["nid"]
                if res.get("wait"):
                    info = dict(res["wait"])
                    info.setdefault("execution_id", execution.id)
                    info.setdefault("created_at", time.strftime("%Y-%m-%d %H:%M:%S"))
                    pending.append(info)
                    continue
                exc = res.get("exc")
                if exc is None:
                    continue
                if node_states.get(nid) not in ("failed", "timeout"):
                    node_states[nid] = "failed"
                for ei in outgoing[nid]:
                    edge_active[ei] = False
                msg = (
                    exc.detail
                    if isinstance(exc, HTTPException)
                    else f"{type(exc).__name__}: {exc}"
                )
                if on_error != "continue":
                    execution.error = f"节点 {nid} 失败：{msg}"
                    fatal = True
            await db.commit()
            if fatal:
                execution.state = "failed"
                _skip_downstream()
                break

        waiting = [nid for nid, st in node_states.items() if st == "waiting"]
        if waiting:
            execution.state = "waiting"
        elif execution.state == "running":
            execution.state = "succeeded"
    except Exception as exc:  # noqa: BLE001
        execution.state = "failed"
        execution.error = f"{type(exc).__name__}: {exc}"
        _skip_downstream()


def _finalize_execution(
    execution: WorkflowExecution,
    nodes: dict[str, Any],
    state: dict[str, Any],
    conversation: WorkflowConversation | None,
    ctx: dict[str, Any],
) -> None:
    outputs = state["outputs"]
    end_outputs = {
        nid: outputs[nid]
        for nid in nodes
        if _canon_type(nodes[nid]["type"]) == "end" and nid in outputs
    }
    final_outputs: dict[str, Any] = dict(end_outputs) if end_outputs else dict(outputs)
    answer = collect_answers(nodes, outputs)
    if answer:
        final_outputs["_answer"] = answer
    execution.outputs = final_outputs
    execution.node_states = state["node_states"]
    execution.runtime = {
        "edge_active": state["edge_active"],
        "pending": state.get("pending") or [],
        "conversation": dict(ctx.get("conversation") or {}),
    }
    if conversation is not None:
        conversation.variables = dict(ctx.get("conversation") or {})


async def execute_workflow(
    db: AsyncSession,
    user: User,
    cap: Capability,
    input_data: dict[str, Any],
    conversation: WorkflowConversation | None = None,
) -> WorkflowExecution:
    definition = load_workflow_definition(cap)
    validate_definition(definition)
    nodes = {n["id"]: n for n in definition["nodes"]}
    edges = definition.get("edges") or []
    incoming, outgoing = _index_edges(nodes, edges)

    history: list[dict[str, str]] = []
    conv_vars: dict[str, Any] = {}
    if conversation is not None:
        conv_cfg = definition.get("conversation") or {}
        history = await _load_conversation_history(
            db, conversation, int(conv_cfg.get("history_turns") or 10) * 2
        )
        conv_vars = dict(conversation.variables or {})
    ctx = _build_ctx(
        input_data,
        history,
        conv_vars,
        conversation.id if conversation is not None else "",
    )
    state: dict[str, Any] = {
        "outputs": {},
        "node_states": {nid: "pending" for nid in nodes},
        "edge_active": [True] * len(edges),
        "pending": [],
    }
    execution = WorkflowExecution(
        workflow_id=cap.id,
        state="running",
        input_data=input_data,
        node_states=state["node_states"],
        conversation_id=conversation.id if conversation is not None else "",
        created_by=user.id,
    )
    db.add(execution)
    await db.flush()
    if cap.status in ("published", "deprecated", "reviewing"):
        await record_usage(db, user, cap, "workflow_execute", {"input": input_data})
    await db.commit()

    await _run_schedule(
        db,
        user,
        nodes,
        edges,
        incoming,
        outgoing,
        ctx,
        state,
        execution,
        overall_timeout=int(definition.get("timeout_seconds") or 0),
        on_error=definition.get("on_error", "fail"),
    )
    _finalize_execution(execution, nodes, state, conversation, ctx)
    await db.commit()
    await db.refresh(execution)
    return execution


async def resume_workflow(
    db: AsyncSession,
    user: User,
    cap: Capability,
    execution: WorkflowExecution,
    node_id: str,
    approved: bool,
    comment: str = "",
    approver: str = "",
) -> WorkflowExecution:
    """审批决定后续跑被暂停的工作流（durable resume）。"""
    definition = load_workflow_definition(cap)
    validate_definition(definition)
    nodes = {n["id"]: n for n in definition["nodes"]}
    edges = definition.get("edges") or []
    incoming, outgoing = _index_edges(nodes, edges)

    rt = dict(execution.runtime or {})
    edge_active = list(rt.get("edge_active") or [])
    if len(edge_active) != len(edges):
        edge_active = [True] * len(edges)
    node_states: dict[str, Any] = dict(
        execution.node_states or {nid: "pending" for nid in nodes}
    )
    outputs: dict[str, Any] = dict(execution.outputs or {})
    outputs.pop("_answer", None)
    if node_states.get(node_id) != "waiting":
        raise HTTPException(status.HTTP_409_CONFLICT, f"节点 {node_id} 不在等待审批")

    branch = "true" if approved else "false"
    outputs[node_id] = {
        "approved": bool(approved),
        "comment": comment,
        "approver": approver,
        "decision_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    }
    node_states[node_id] = "succeeded"
    for ei in outgoing[node_id]:
        cond = str(edges[ei].get("condition") or "").strip().lower()
        edge_active[ei] = (cond == "") or (cond == branch)
    pending = [p for p in (rt.get("pending") or []) if p.get("node_id") != node_id]

    conversation = None
    if execution.conversation_id:
        conversation = await db.get(WorkflowConversation, execution.conversation_id)
    input_data = execution.input_data or {}
    history: list[dict[str, str]] = []
    if conversation is not None:
        conv_cfg = definition.get("conversation") or {}
        history = await _load_conversation_history(
            db, conversation, int(conv_cfg.get("history_turns") or 10) * 2
        )
    conv_vars = dict(
        rt.get("conversation") or (conversation.variables if conversation else {}) or {}
    )
    ctx = _build_ctx(input_data, history, conv_vars, execution.conversation_id or "")
    for nid, out in outputs.items():
        ctx[nid] = out

    state: dict[str, Any] = {
        "outputs": outputs,
        "node_states": node_states,
        "edge_active": edge_active,
        "pending": pending,
    }
    execution.state = "running"
    execution.error = ""
    await db.commit()

    await _run_schedule(
        db,
        user,
        nodes,
        edges,
        incoming,
        outgoing,
        ctx,
        state,
        execution,
        overall_timeout=0,
        on_error=definition.get("on_error", "fail"),
    )
    _finalize_execution(execution, nodes, state, conversation, ctx)
    await db.commit()
    await db.refresh(execution)
    return execution
