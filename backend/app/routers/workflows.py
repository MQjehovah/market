"""工作流 API：JSON 创建草稿包、执行、查询、取消。"""

import io
import json
import zipfile
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import and_, select
from sqlalchemy.orm import joinedload, selectinload

from app.auth import CurrentUser, DbSession
from app.models import Capability, CapabilityArtifact, WorkflowExecution
from app.permissions import can_view, require_runtime_access
from app.schemas import (
    CapabilityOut,
    WorkflowCreate,
    WorkflowExecuteRequest,
    WorkflowExecutionOut,
    WorkflowUpdate,
)
from app.services.capabilities import ensure_name_ownership, normalize_cap_name, to_capability_out
from app.services.marketplace import resolve_capability
from app.services.workflows import execute_workflow, load_workflow_definition, validate_definition
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
        node_states=ex.node_states or {},
        error=ex.error or "",
        created_by=ex.created_by,
        created_at=ex.created_at,
        updated_at=ex.updated_at,
    )


@router.post("/runtime/workflows/{name}/executions", response_model=WorkflowExecutionOut)
async def run_workflow(name: str, data: WorkflowExecuteRequest, db: DbSession, user: CurrentUser):
    cap = await resolve_capability(db, user, name)
    await require_runtime_access(user, cap, db)
    if cap.type != "workflow":
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, f"能力 {name} 不是工作流")
    execution = await execute_workflow(db, user, cap, data.input)
    return _to_out(execution, cap)


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
