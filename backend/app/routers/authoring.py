"""AI 创作助手 API。

- 长耗时生成走后台任务(POST /jobs + 轮询 GET /jobs/{id})，不阻塞请求；
- 保留同步 POST /generate 便于脚本/兼容(短请求)。
写权限 = capability.publish。不落库草稿，前端拿到内容后走编辑器保存/审核流程。
"""

import asyncio

from fastapi import APIRouter, HTTPException, status

from app.auth import CurrentUser, DbSession
from app.database import SessionLocal
from app.models import AuthoringJob
from app.permissions import role_has_permission
from app.schemas import AuthoringJobOut, AuthoringOut, AuthoringRequest
from app.services import authoring
from app.services.agent_runner import is_llm_configured

router = APIRouter(prefix="/api/authoring", tags=["authoring"])


def _require_publish(user) -> None:
    if not role_has_permission(user.role, "capability.publish"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "需要发布权限（Publisher/Admin）")


def _job_out(job: AuthoringJob) -> AuthoringJobOut:
    return AuthoringJobOut(
        job_id=job.id, status=job.status, kind=job.kind or "",
        fields=job.fields or {}, raw=job.raw or "", error=job.error or "",
    )


@router.post("/generate", response_model=AuthoringOut)
async def generate(data: AuthoringRequest, user: CurrentUser) -> AuthoringOut:
    _require_publish(user)
    body = await authoring.generate(
        kind=data.kind, name=data.name, description=data.description,
        instruction=data.instruction, current=data.current,
    )
    return AuthoringOut(**body)


@router.post("/jobs", response_model=AuthoringJobOut)
async def create_job(data: AuthoringRequest, user: CurrentUser) -> AuthoringJobOut:
    """创建后台创作任务，立即返回 job_id；前端轮询 GET /jobs/{id}。"""
    _require_publish(user)
    if not is_llm_configured():
        raise HTTPException(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "市场未配置 LLM（需 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL），无法使用 AI 创作",
        )
    async with SessionLocal() as db:
        job = AuthoringJob(
            user_id=user.id, kind=data.kind, status="pending",
            request=data.model_dump(),
        )
        db.add(job)
        await db.commit()
        await db.refresh(job)
        out = _job_out(job)
    asyncio.create_task(authoring.run_job(out.job_id))
    return out


@router.get("/jobs/{job_id}", response_model=AuthoringJobOut)
async def get_job(job_id: str, db: DbSession, user: CurrentUser) -> AuthoringJobOut:
    job = await db.get(AuthoringJob, job_id)
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "任务不存在")
    if job.user_id != user.id and not role_has_permission(user.role, "*"):
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权查看该任务")
    return _job_out(job)
