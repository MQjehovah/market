"""工作流引擎：解析 workflow.json → 拓扑排序 → 逐节点执行 → 变量传递 → 记录用量。

节点类型：
    tool  → 市场沙箱真实执行（invoke_tool）
    agent → A2A 委派（send_task）
    skill → 技能激活，返回执行指引（activate_skill）
    mcp   → MCP 安装/连接配置下发（install_mcp）

变量模板：${input.key} 引用执行入参；${nodeId.outputKey} 引用前置节点输出。
"""

import asyncio
import io
import json
import re
import zipfile
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.a2a.protocol import extract_text
from app.a2a.service import send_task
from app.models import Capability, User, WorkflowExecution
from app.services.marketplace import (
    activate_skill,
    install_mcp,
    invoke_tool,
    record_usage,
    resolve_capability,
)
from app.storage import get_storage

_TEMPLATE_RE = re.compile(r"\$\{([^}]+)\}")
_NODE_TYPES = {"tool", "agent", "skill", "mcp"}


def load_workflow_definition(cap: Capability) -> dict[str, Any]:
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
    validate_definition(definition)
    return definition


def validate_definition(definition: dict[str, Any]) -> list[str]:
    """校验节点/边并返回拓扑顺序；非法或存在环时抛 422。"""
    nodes = definition.get("nodes")
    if not isinstance(nodes, list) or not nodes:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "workflow.json 必须包含非空 nodes")

    node_ids: list[str] = []
    for node in nodes:
        if not isinstance(node, dict) or not node.get("id") or not node.get("type"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, "每个节点必须包含 id 与 type"
            )
        if node["type"] not in _NODE_TYPES:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"节点 {node['id']} 类型未知：{node['type']}（可选 {sorted(_NODE_TYPES)}）",
            )
        if not node.get("capability"):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY, f"节点 {node['id']} 缺少 capability"
            )
        node_ids.append(node["id"])
    if len(set(node_ids)) != len(node_ids):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "节点 id 不能重复")

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


def render_value(value: Any, ctx: dict[str, Any]) -> Any:
    """递归渲染 ${path} 模板；path 从 ctx 取值（input 或节点 id 输出）。"""
    if isinstance(value, dict):
        return {k: render_value(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [render_value(v, ctx) for v in value]
    if isinstance(value, str):
        def _replace(match: re.Match) -> str:
            path = match.group(1).strip().split(".")
            cur: Any = ctx
            for part in path:
                if not isinstance(cur, dict) or part not in cur:
                    return ""
                cur = cur[part]
            if isinstance(cur, (str, int, float, bool)):
                return str(cur)
            return json.dumps(cur, ensure_ascii=False)

        return _TEMPLATE_RE.sub(_replace, value)
    return value


async def _run_node(
    db: AsyncSession,
    user: User,
    node: dict[str, Any],
    ctx: dict[str, Any],
) -> dict[str, Any]:
    cap = await resolve_capability(db, user, node["capability"], node.get("version") or None)
    params = render_value(node.get("params") or {}, ctx)
    ntype = node["type"]

    if ntype == "tool":
        result = await invoke_tool(db, user, cap, params)
        if result.get("status") == "error":
            raise HTTPException(
                status.HTTP_502_BAD_GATEWAY,
                f"工具 {cap.name} 执行失败：{result.get('error')}",
            )
        return {"output": result}
    if ntype == "agent":
        task_text = str(params.get("task") or params.get("text") or "")
        task = await send_task(
            db,
            user,
            cap,
            client_task_id=f"wf-{node['id']}",
            message={"role": "user", "parts": [{"type": "text", "text": task_text}]},
            metadata={"source": "workflow", "node": node["id"]},
        )
        text = extract_text(task.output_message or {}) if task.output_message else ""
        return {
            "output": {
                "task_id": task.id,
                "state": task.state,
                "text": text,
                "agent": cap.name,
            }
        }
    if ntype == "skill":
        return {"output": await activate_skill(db, user, cap, str(params.get("context") or ""))}
    if ntype == "mcp":
        return {"output": await install_mcp(db, user, cap, params.get("config") or {})}
    raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"未知节点类型 {ntype}")


async def execute_workflow(
    db: AsyncSession,
    user: User,
    cap: Capability,
    input_data: dict[str, Any],
) -> WorkflowExecution:
    definition = load_workflow_definition(cap)
    order = validate_definition(definition)
    nodes = {n["id"]: n for n in definition["nodes"]}
    node_states: dict[str, Any] = {nid: "pending" for nid in nodes}
    outputs: dict[str, Any] = {}
    ctx: dict[str, Any] = {"input": input_data}

    execution = WorkflowExecution(
        workflow_id=cap.id,
        state="running",
        input_data=input_data,
        node_states=node_states,
        created_by=user.id,
    )
    db.add(execution)
    await db.flush()
    await record_usage(db, user, cap, "workflow_execute", {"input": input_data})
    await db.commit()

    on_error = definition.get("on_error", "fail")
    overall_timeout = int(definition.get("timeout_seconds") or 0)
    try:
        for nid in order:
            if execution.state == "canceled":
                break
            node = nodes[nid]
            if overall_timeout:
                elapsed = (execution.updated_at.astimezone() - execution.created_at.astimezone()).total_seconds()
                if elapsed >= overall_timeout:
                    execution.state = "failed"
                    execution.error = f"工作流超时（>{overall_timeout}s）"
                    for rest in order[order.index(nid):]:
                        node_states[rest] = "skipped"
                    break
            node_states[nid] = "running"
            await db.commit()
            node_timeout = int(node.get("timeout_seconds") or 120)
            retries = int(node.get("retries") or 0)
            attempt = 0
            result: dict[str, Any] = {}
            while True:
                try:
                    result = await asyncio.wait_for(
                        _run_node(db, user, node, ctx), timeout=node_timeout
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
            node_output = result.get("output") or {}
            outputs[nid] = node_output
            ctx[nid] = node_output
            node_states[nid] = "succeeded"
            await db.commit()
        if execution.state != "canceled":
            execution.state = "succeeded"
    except HTTPException as exc:
        execution.state = "failed"
        execution.error = str(exc.detail)
    except Exception as exc:  # noqa: BLE001
        execution.state = "failed"
        execution.error = f"{type(exc).__name__}: {exc}"
        if on_error != "continue":
            for nid, st in node_states.items():
                if st == "pending":
                    node_states[nid] = "skipped"

    execution.outputs = outputs
    execution.node_states = node_states
    await db.commit()
    await db.refresh(execution)
    return execution
