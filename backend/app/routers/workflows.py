"""工作流 API：JSON 创建草稿包、执行、查询、取消。"""

import io
import json
import zipfile
from datetime import datetime

import hmac

from fastapi import APIRouter, HTTPException, Request, status
from sqlalchemy import and_, select
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, DbSession
from app.models import (
    Capability,
    CapabilityArtifact,
    User,
    WorkflowConversation,
    WorkflowExecution,
)
from app.permissions import can_view, require_workflow_run
from app.schemas import (
    CapabilityOut,
    MessageOut,
    WorkflowApprovalOut,
    WorkflowApprovalRequest,
    WorkflowChatOut,
    WorkflowChatRequest,
    WorkflowConversationOut,
    WorkflowCreate,
    WorkflowExecuteRequest,
    WorkflowExecutionOut,
    WorkflowMessageOut,
    WorkflowRunMetaOut,
    WorkflowRunRequest,
    WorkflowUpdate,
)
from app.services.capabilities import ensure_name_ownership, normalize_cap_name, to_capability_out
from app.services.marketplace import resolve_capability
from app.services.workflow_triggers import gitlab_trigger_input
from app.services.workflows import (
    execute_workflow,
    extract_run_meta,
    load_workflow_definition,
    resume_workflow,
    validate_definition,
)
from app.storage import get_storage

router = APIRouter(prefix="/api", tags=["workflows"])


def _workflow_zip(workflow: dict) -> bytes:
    validate_definition(workflow, require_nodes=False)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("workflow.json", json.dumps(workflow, ensure_ascii=False, indent=2))
        zf.writestr(
            "README.md",
            "# 工作流\n\n由工作流设计器生成。节点引用市场上的 tool/agent/skill/mcp 能力。\n",
        )
    return buf.getvalue()


@router.post("/workflows", response_model=CapabilityOut, status_code=status.HTTP_201_CREATED)
async def create_workflow(data: WorkflowCreate, db: DbSession, user: CurrentUser):
    """从 workflow.json 直接创建 workflow 能力（draft + 能力包）。"""
    name = normalize_cap_name(data.name)
    exists = await db.scalar(
        select(Capability.id).where(
            and_(
                Capability.name == name,
                Capability.version == data.version,
                Capability.type == "workflow",
            )
        )
    )
    if exists:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"工作流 {name} 已存在版本 {data.version}"
        )
    # 名称归属：同名只能由既有作者继续发版本（防抢注同名后污染按名资源）
    await ensure_name_ownership(db, name, user)
    pkg = _workflow_zip(data.workflow)
    from app.services.packages import extract_readme_text

    cap = Capability(
        name=name,
        description=data.description,
        type="workflow",
        version=data.version,
        category=data.category,
        tags=data.tags,
        visibility=data.visibility,
        access_policy=data.access_policy,
        allowed_users=data.allowed_users,
        status="draft",
        author_id=user.id,
        organization=user.department,
        readme_md=extract_readme_text(pkg),
    )
    db.add(cap)
    await db.flush()
    info = get_storage().save(cap.id, f"{cap.name}-{cap.version}.zip", io.BytesIO(pkg))
    db.add(CapabilityArtifact(capability_id=cap.id, filename=f"{cap.name}-{cap.version}.zip", **info))
    await db.commit()
    await db.refresh(cap)
    return to_capability_out(cap, author_name=user.username)


async def _get_workflow_cap(db: DbSession, cap_id: str) -> Capability:
    cap = await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts), joinedload(Capability.author))
        .where(Capability.id == cap_id)
    )
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "能力不存在")
    if cap.type != "workflow":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"能力 {cap.name} 不是工作流")
    return cap


def _require_workflow_owner(cap: Capability, user) -> None:
    if user.role != "admin" and cap.author_id != user.id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, "只能编辑自己创建的工作流草稿"
        )


def _load_workflow(cap: Capability) -> dict:
    """返回工作流定义；未上传能力包时给出空画布结构。"""
    if not cap.artifacts:
        return {"nodes": [], "edges": []}
    return load_workflow_definition(cap, require_nodes=False)


@router.get("/workflows/templates")
async def workflow_templates():
    """内置示例工作流模板（Dify 对齐），供「从模板新建」。"""
    from app.services.workflow_samples import (
        CHANGE_APPROVAL_WORKFLOW,
        IT_ALERT_WORKFLOW,
        KNOWLEDGE_QA_CHATFLOW,
    )

    samples = [IT_ALERT_WORKFLOW, KNOWLEDGE_QA_CHATFLOW, CHANGE_APPROVAL_WORKFLOW]
    return [
        {
            "name": w.get("name", ""),
            "description": w.get("description", ""),
            "mode": w.get("mode", "workflow"),
            "workflow": w,
        }
        for w in samples
    ]


@router.get("/workflows/{cap_id}/definition")
async def workflow_definition(cap_id: str, db: DbSession, user: CurrentUser):
    """读取工作流定义（画布数据）。草稿仅作者/管理员；正式版按可见性。"""
    cap = await _get_workflow_cap(db, cap_id)
    if cap.status in ("draft", "rejected", "returned"):
        _require_workflow_owner(cap, user)
    elif not can_view(cap, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限查看该能力")
    return {
        "capability": to_capability_out(cap, author_name=cap.author.username if cap.author else ""),
        "workflow": _load_workflow(cap),
    }


@router.put("/workflows/{cap_id}", response_model=CapabilityOut)
async def update_workflow(cap_id: str, data: WorkflowUpdate, db: DbSession, user: CurrentUser):
    """更新工作流草稿的能力包（workflow.json），仅草稿/驳回/打回状态可编辑。"""
    cap = await _get_workflow_cap(db, cap_id)
    _require_workflow_owner(cap, user)
    if cap.status not in ("draft", "returned", "rejected"):
        raise HTTPException(
            status.HTTP_409_CONFLICT, "仅草稿或被打回/驳回的工作流可以编辑"
        )
    pkg = _workflow_zip(data.workflow)
    from app.services.packages import extract_readme_text

    filename = f"{cap.name}-{cap.version}-draft.zip"
    info = get_storage().save(cap.id, filename, io.BytesIO(pkg))
    db.add(
        CapabilityArtifact(
            capability_id=cap.id,
            filename=filename,
            **info,
        )
    )
    cap.readme_md = extract_readme_text(pkg)
    cap.updated_at = datetime.utcnow()
    await db.commit()
    await db.refresh(cap)
    return to_capability_out(cap, author_name=user.username)


@router.post("/workflows/{cap_id}/test", response_model=WorkflowExecutionOut)
async def test_workflow(cap_id: str, data: WorkflowExecuteRequest, db: DbSession, user: CurrentUser):
    """试运行当前草稿定义：作者/管理员可直接执行，不要求发布。"""
    cap = await _get_workflow_cap(db, cap_id)
    _require_workflow_owner(cap, user)
    if not cap.artifacts:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_ENTITY, "请先保存画布，再试运行"
        )
    execution = await execute_workflow(db, user, cap, data.input)
    return _to_out(execution, cap)


def _to_out(ex: WorkflowExecution, cap: Capability | None) -> WorkflowExecutionOut:
    return WorkflowExecutionOut(
        id=ex.id,
        workflow_id=ex.workflow_id,
        workflow_name=cap.name if cap else "",
        state=ex.state,
        input_data=ex.input_data or {},
        outputs=ex.outputs or {},
        node_outputs=(ex.runtime or {}).get("node_outputs") or (ex.outputs or {}),
        node_states=ex.node_states or {},
        pending=(ex.runtime or {}).get("pending") or [],
        error=ex.error or "",
        created_by=ex.created_by,
        created_at=ex.created_at,
        updated_at=ex.updated_at,
    )


@router.post("/runtime/workflows/{name}/executions", response_model=WorkflowExecutionOut)
async def run_workflow(name: str, data: WorkflowExecuteRequest, db: DbSession, user: CurrentUser):
    cap = await resolve_capability(db, user, name)
    await require_workflow_run(user, cap, db)
    if cap.type != "workflow":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"能力 {name} 不是工作流")
    execution = await execute_workflow(db, user, cap, data.input)
    return _to_out(execution, cap)


@router.get("/runtime/workflows/{name}/run-meta", response_model=WorkflowRunMetaOut)
async def workflow_run_meta(name: str, db: DbSession, user: CurrentUser):
    """工作流"作为应用"的对外运行元信息（形态/输入字段/输出/触发）。"""
    cap = await resolve_capability(db, user, name)
    await require_workflow_run(user, cap, db)
    if cap.type != "workflow":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"能力 {name} 不是工作流")
    definition = load_workflow_definition(cap, require_nodes=False)
    meta = extract_run_meta(definition)
    return WorkflowRunMetaOut(**meta)


@router.post("/runtime/workflows/{name}/run", response_model=WorkflowExecutionOut)
async def run_workflow_app(name: str, data: WorkflowRunRequest, db: DbSession, user: CurrentUser):
    """按对外输入字段校验并补默认后运行（表单/手动触发用）。"""
    cap = await resolve_capability(db, user, name)
    await require_workflow_run(user, cap, db)
    if cap.type != "workflow":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"能力 {name} 不是工作流")
    definition = load_workflow_definition(cap, require_nodes=False)
    fields = extract_run_meta(definition)["input_fields"]
    payload = dict(data.input or {})
    for f in fields:
        key = f["key"]
        if payload.get(key) in (None, ""):
            if f.get("default") not in (None, ""):
                payload[key] = f["default"]
            elif f.get("required"):
                raise HTTPException(
                    status.HTTP_422_UNPROCESSABLE_ENTITY, f"缺少必填输入：{f.get('label') or key}"
                )
    execution = await execute_workflow(db, user, cap, payload)
    return _to_out(execution, cap)


@router.get(
    "/runtime/workflows/{name}/executions", response_model=list[WorkflowExecutionOut]
)
async def list_workflow_executions(
    name: str, db: DbSession, user: CurrentUser, limit: int = 20
):
    """近期执行记录（本人；admin 全量）。"""
    cap = await resolve_capability(db, user, name)
    await require_workflow_run(user, cap, db)
    stmt = select(WorkflowExecution).where(WorkflowExecution.workflow_id == cap.id)
    if user.role != "admin":
        stmt = stmt.where(WorkflowExecution.created_by == user.id)
    rows = list(
        (
            await db.scalars(
                stmt.order_by(WorkflowExecution.created_at.desc()).limit(max(1, min(limit, 100)))
            )
        ).all()
    )
    return [_to_out(ex, cap) for ex in rows]


@router.post("/runtime/workflows/{name}/trigger", response_model=WorkflowExecutionOut)
async def trigger_workflow(name: str, request: Request, db: DbSession):
    """webhook / GitLab 触发：按 workflow.json 的 `trigger` 校验并映射入参。"""
    cap = await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts))
        .where(
            Capability.name == name,
            Capability.type == "workflow",
            Capability.status == "published",
        )
    )
    if cap is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"工作流 {name} 不存在或未发布")
    definition = load_workflow_definition(cap)
    trig = definition.get("trigger") or {}
    ttype = str(trig.get("type") or "")
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    if not isinstance(body, dict):
        body = {}
    headers = {k.lower(): v for k, v in request.headers.items()}
    if ttype == "gitlab":
        input_data = await gitlab_trigger_input(db, trig, headers, body)
    elif ttype == "webhook":
        token = str(trig.get("token") or "")
        provided = headers.get("x-workflow-token") or ""
        if not token or not hmac.compare_digest(token, provided):
            raise HTTPException(status.HTTP_401_UNAUTHORIZED, "无效的 X-Workflow-Token")
        input_data = body.get("input") or {}
    else:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "该工作流未启用触发")
    author = await db.get(User, cap.author_id)
    if author is None:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "工作流作者缺失")
    execution = await execute_workflow(db, author, cap, input_data)
    return _to_out(execution, cap)


def _conv_out(conv: WorkflowConversation, workflow_name: str = "") -> WorkflowConversationOut:
    return WorkflowConversationOut(
        id=conv.id,
        workflow_id=conv.workflow_id,
        workflow_name=workflow_name,
        title=conv.title or "",
        variables=dict(conv.variables or {}),
        created_at=conv.created_at,
        updated_at=conv.updated_at,
    )


@router.post("/runtime/workflows/{name}/chat", response_model=WorkflowChatOut)
async def chat_workflow(name: str, data: WorkflowChatRequest, db: DbSession, user: CurrentUser):
    """会话模式（chatflow）：按 conversation_id 持久化会话变量与多轮历史。"""
    cap = await resolve_capability(db, user, name)
    await require_workflow_run(user, cap, db)
    if cap.type != "workflow":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"能力 {name} 不是工作流")
    conv: WorkflowConversation | None = None
    if data.conversation_id:
        conv = await db.get(WorkflowConversation, data.conversation_id)
        if conv is None or conv.workflow_id != cap.id:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
        if user.role != "admin" and conv.created_by != user.id:
            raise HTTPException(status.HTTP_403_FORBIDDEN, "无权访问该会话")
    if conv is None:
        conv = WorkflowConversation(workflow_id=cap.id, created_by=user.id, title=data.query[:60])
        db.add(conv)
        await db.flush()
    input_data = {"query": data.query, **(data.inputs or {})}
    execution = await execute_workflow(db, user, cap, input_data, conversation=conv)
    raw_outputs = execution.outputs or {}
    return WorkflowChatOut(
        conversation_id=conv.id,
        execution_id=execution.id,
        state=execution.state,
        answer=str(raw_outputs.get("_answer") or ""),
        outputs={k: v for k, v in raw_outputs.items() if k != "_answer"},
        variables=dict(conv.variables or {}),
        error=execution.error or "",
    )


@router.get(
    "/runtime/workflows/{name}/conversations",
    response_model=list[WorkflowConversationOut],
)
async def list_conversations(name: str, db: DbSession, user: CurrentUser):
    cap = await resolve_capability(db, user, name)
    stmt = select(WorkflowConversation).where(WorkflowConversation.workflow_id == cap.id)
    if user.role != "admin":
        stmt = stmt.where(WorkflowConversation.created_by == user.id)
    rows = list(
        (await db.scalars(stmt.order_by(WorkflowConversation.updated_at.desc()))).all()
    )
    return [_conv_out(r, cap.name) for r in rows]


@router.get(
    "/runtime/workflows/conversations/{conversation_id}/messages",
    response_model=list[WorkflowMessageOut],
)
async def conversation_messages(conversation_id: str, db: DbSession, user: CurrentUser):
    conv = await db.get(WorkflowConversation, conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    if user.role != "admin" and conv.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权访问该会话")
    rows = list(
        (
            await db.scalars(
                select(WorkflowExecution)
                .where(WorkflowExecution.conversation_id == conv.id)
                .order_by(WorkflowExecution.created_at.asc())
            )
        ).all()
    )
    messages: list[WorkflowMessageOut] = []
    for ex in rows:
        query = str((ex.input_data or {}).get("query") or "")
        answer = str((ex.outputs or {}).get("_answer") or "")
        if query:
            messages.append(
                WorkflowMessageOut(
                    role="user", content=query, execution_id=ex.id,
                    state=ex.state, created_at=ex.created_at,
                )
            )
        if answer:
            messages.append(
                WorkflowMessageOut(
                    role="assistant", content=answer, execution_id=ex.id,
                    state=ex.state, created_at=ex.created_at,
                )
            )
    return messages


@router.delete(
    "/runtime/workflows/conversations/{conversation_id}", response_model=MessageOut
)
async def delete_conversation(conversation_id: str, db: DbSession, user: CurrentUser):
    conv = await db.get(WorkflowConversation, conversation_id)
    if conv is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "会话不存在")
    if user.role != "admin" and conv.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权删除该会话")
    await db.delete(conv)
    await db.commit()
    return MessageOut(message="会话已删除")


def _can_approve(execution: WorkflowExecution, item: dict, user) -> bool:
    if user.role == "admin" or execution.created_by == user.id:
        return True
    assignee = str(item.get("assignee") or "")
    if not assignee:
        return False
    return assignee in {user.username, user.role, getattr(user, "department", "")}


@router.get("/runtime/workflows/approvals", response_model=list[WorkflowApprovalOut])
async def list_approvals(db: DbSession, user: CurrentUser):
    """当前用户可见的待审批项（管理员全部；否则本人发起或指派给我的）。"""
    rows = list(
        (
            await db.scalars(
                select(WorkflowExecution).where(WorkflowExecution.state == "waiting")
            )
        ).all()
    )
    out: list[WorkflowApprovalOut] = []
    for ex in rows:
        pending = (ex.runtime or {}).get("pending") or []
        if not pending:
            continue
        cap = await db.get(Capability, ex.workflow_id)
        for item in pending:
            if not _can_approve(ex, item, user):
                continue
            out.append(
                WorkflowApprovalOut(
                    execution_id=ex.id,
                    workflow_name=cap.name if cap else "",
                    node_id=str(item.get("node_id") or ""),
                    title=str(item.get("title") or ""),
                    description=str(item.get("description") or ""),
                    assignee=str(item.get("assignee") or ""),
                    created_by=ex.created_by,
                    created_at=ex.created_at,
                )
            )
    return out


@router.post(
    "/runtime/workflows/executions/{exec_id}/approve",
    response_model=WorkflowExecutionOut,
)
async def approve_execution(
    exec_id: str, data: WorkflowApprovalRequest, db: DbSession, user: CurrentUser
):
    """对等待中的审批节点做出通过/驳回决定，并续跑工作流。"""
    execution = await db.get(WorkflowExecution, exec_id)
    if execution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "执行记录不存在")
    if execution.state != "waiting":
        raise HTTPException(status.HTTP_409_CONFLICT, "该执行当前不在等待审批")
    pending = (execution.runtime or {}).get("pending") or []
    item = next((p for p in pending if str(p.get("node_id")) == data.node_id), None)
    if item is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "该节点不在待审批列表")
    if not _can_approve(execution, item, user):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权审批该节点")
    cap = await db.scalar(
        select(Capability)
        .options(selectinload(Capability.artifacts))
        .where(Capability.id == execution.workflow_id)
    )
    if cap is None or cap.type != "workflow":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, "工作流能力缺失")
    result = await resume_workflow(
        db,
        user,
        cap,
        execution,
        data.node_id,
        data.approved,
        data.comment,
        approver=user.username,
    )
    return _to_out(result, cap)


@router.get("/runtime/workflows/executions/{exec_id}", response_model=WorkflowExecutionOut)
async def execution_detail(exec_id: str, db: DbSession, user: CurrentUser):
    execution = await db.get(WorkflowExecution, exec_id)
    if execution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "执行记录不存在")
    if user.role != "admin" and execution.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限查看该执行记录")
    cap = await db.get(Capability, execution.workflow_id)
    return _to_out(execution, cap)


@router.post("/runtime/workflows/executions/{exec_id}/cancel", response_model=WorkflowExecutionOut)
async def cancel_execution(exec_id: str, db: DbSession, user: CurrentUser):
    execution = await db.get(WorkflowExecution, exec_id)
    if execution is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "执行记录不存在")
    if user.role != "admin" and execution.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限取消该执行记录")
    if execution.state in ("pending", "running"):
        execution.state = "canceled"
        for nid, st in (execution.node_states or {}).items():
            if st in ("pending", "running"):
                execution.node_states[nid] = "skipped"
        await db.commit()
        await db.refresh(execution)
    cap = await db.get(Capability, execution.workflow_id)
    return _to_out(execution, cap)
