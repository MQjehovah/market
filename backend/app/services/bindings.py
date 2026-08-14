"""Agent 动态绑定：CRUD + 默认绑定解析（运行时组装，不生成能力包）。"""

from fastapi import HTTPException, status
from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import AgentBinding, Capability, User
from app.schemas import AgentBindingCreate, AgentBindingUpdate


async def list_bindings(db: AsyncSession, agent: Capability) -> list[AgentBinding]:
    rows = (
        await db.scalars(
            select(AgentBinding)
            .where(AgentBinding.agent_id == agent.id)
            .order_by(AgentBinding.created_at.asc())
        )
    ).all()
    return list(rows)


async def get_binding(
    db: AsyncSession, user: User, binding_id: str, agent: Capability
) -> AgentBinding:
    binding = await db.scalar(
        select(AgentBinding).where(
            and_(AgentBinding.id == binding_id, AgentBinding.agent_id == agent.id)
        )
    )
    if binding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "绑定不存在")
    if user.role != "admin" and binding.created_by != user.id:
        raise HTTPException(status.HTTP_403_FORBIDDEN, "无权限操作该绑定")
    return binding


async def resolve_binding(
    db: AsyncSession, agent: Capability, ref: str = ""
) -> AgentBinding | None:
    """按名称/ID 解析绑定；ref 为空时取第一个启用的绑定作为默认绑定。"""
    if ref:
        binding = await db.scalar(
            select(AgentBinding).where(
                and_(
                    AgentBinding.agent_id == agent.id,
                    or_(AgentBinding.id == ref, AgentBinding.name == ref),
                )
            )
        )
        if binding is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, f"绑定 {ref} 不存在")
        return binding
    binding = await db.scalar(
        select(AgentBinding)
        .where(
            and_(AgentBinding.agent_id == agent.id, AgentBinding.enabled.is_(True))
        )
        .order_by(AgentBinding.created_at.asc())
    )
    return binding


async def create_binding(
    db: AsyncSession, user: User, agent: Capability, data: AgentBindingCreate
) -> AgentBinding:
    exists = await db.scalar(
        select(AgentBinding.id).where(
            and_(AgentBinding.agent_id == agent.id, AgentBinding.name == data.name)
        )
    )
    if exists:
        raise HTTPException(status.HTTP_409_CONFLICT, f"该 Agent 已存在绑定 {data.name}")
    binding = AgentBinding(
        agent_id=agent.id,
        name=data.name,
        description=data.description,
        version=data.version,
        dependencies=[d.model_dump() for d in data.dependencies],
        enabled=data.enabled,
        created_by=user.id,
    )
    db.add(binding)
    await db.commit()
    await db.refresh(binding)
    return binding


async def update_binding(
    db: AsyncSession, binding: AgentBinding, data: AgentBindingUpdate
) -> AgentBinding:
    if data.name is not None:
        binding.name = data.name
    if data.description is not None:
        binding.description = data.description
    if data.version is not None:
        binding.version = data.version
    if data.dependencies is not None:
        binding.dependencies = [d.model_dump() for d in data.dependencies]
    if data.enabled is not None:
        binding.enabled = data.enabled
    await db.commit()
    await db.refresh(binding)
    return binding


async def delete_binding(db: AsyncSession, binding: AgentBinding) -> None:
    await db.delete(binding)
    await db.commit()
