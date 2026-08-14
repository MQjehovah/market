"""工作流 API：JSON 创建草稿包、执行、查询、取消。"""

import io
import json
import zipfile

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import and_, select

from app.auth import CurrentUser, DbSession
from app.models import Capability, CapabilityArtifact, WorkflowExecution
from app.schemas import (
    CapabilityOut,
    WorkflowCreate,
    WorkflowExecuteRequest,
    WorkflowExecutionOut,
)
from app.services.capabilities import to_capability_out
from app.services.marketplace import resolve_capability
from app.services.workflows import execute_workflow, validate_definition
from app.storage import get_storage

router = APIRouter(prefix="/api", tags=["workflows"])


def _require_publisher(user) -> None:
    if user.role not in ("admin", "publisher"):
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "普通用户无权限创建工作流，请联系管理员开通发布权限",
        )


def _workflow_zip(workflow: dict) -> bytes:
    validate_definition(workflow)
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
    _require_publisher(user)
    exists = await db.scalar(
        select(Capability.id).where(
            and_(
                Capability.name == data.name,
                Capability.version == data.version,
                Capability.type == "workflow",
            )
        )
    )
    if exists:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"工作流 {data.name} 已存在版本 {data.version}"
        )
    pkg = _workflow_zip(data.workflow)
    cap = Capability(
        name=data.name,
        description=data.description,
        type="workflow",
        version=data.version,
        category=data.category,
        tags=data.tags,
        visibility=data.visibility,
        status="draft",
        author_id=user.id,
        organization=user.organization,
    )
    db.add(cap)
    await db.flush()
    info = get_storage().save(cap.id, f"{cap.name}-{cap.version}.zip", io.BytesIO(pkg))
    db.add(CapabilityArtifact(capability_id=cap.id, filename=f"{cap.name}-{cap.version}.zip", **info))
    await db.commit()
    await db.refresh(cap)
    return to_capability_out(cap, author_name=user.username)


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
