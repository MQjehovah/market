"""数据模型：用户 / 能力 / 工件 / 审核 / 评分 / 订阅 / 用量 / 通知。"""

import uuid
from datetime import datetime

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.ext.mutable import MutableDict, MutableList

from app.database import Base


def _uuid() -> str:
    return str(uuid.uuid4())


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), default="")
    role: Mapped[str] = mapped_column(String(16), default="user")  # admin | publisher | user
    organization: Mapped[str] = mapped_column(String(100), default="")
    team: Mapped[str] = mapped_column(String(100), default="")
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    capabilities: Mapped[list["Capability"]] = relationship(back_populates="author")


class Capability(Base):
    """能力元数据。同一逻辑能力的不同版本各自成行，由 (name, version) 唯一约束。"""

    __tablename__ = "capabilities"
    __table_args__ = (
        UniqueConstraint("name", "version", name="uq_capability_name_version"),
        Index("ix_capabilities_type_status_visibility", "type", "status", "visibility"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(16), index=True, nullable=False)  # agent|tool|skill|mcp|workflow|plugin|rule|command|hook
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    changelog: Mapped[str] = mapped_column(Text, default="")
    readme_md: Mapped[str] = mapped_column(Text, default="")
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft | reviewing | published | deprecated | archived | rejected | returned
    category: Mapped[str] = mapped_column(String(100), default="", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    input_schema: Mapped[dict] = mapped_column(JSON, default=dict)  # 工具参数定义（来自 schema.json）
    author_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    organization: Mapped[str] = mapped_column(String(100), default="")
    visibility: Mapped[str] = mapped_column(String(16), default="internal")
    # private | team | internal | public
    access_policy: Mapped[str] = mapped_column(String(20), default="open")
    # open（所有登录用户可加入并调用）| admin_only（仅管理员/作者）| restricted（白名单用户名）
    allowed_users: Mapped[list] = mapped_column(JSON, default=list)
    install_policy: Mapped[str] = mapped_column(String(20), default="optional")
    # optional | default_on | required
    validation_report: Mapped[dict] = mapped_column(JSON, default=dict)
    # 最近一次上传包的结构校验摘要：{ok, warnings, errors?, files}
    usage_count: Mapped[int] = mapped_column(Integer, default=0)
    rating_sum: Mapped[float] = mapped_column(Float, default=0.0)
    rating_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    author: Mapped["User"] = relationship(back_populates="capabilities")
    artifacts: Mapped[list["CapabilityArtifact"]] = relationship(
        back_populates="capability", cascade="all, delete-orphan"
    )
    reviews: Mapped[list["Review"]] = relationship(
        back_populates="capability", cascade="all, delete-orphan"
    )
    ratings: Mapped[list["Rating"]] = relationship(
        back_populates="capability", cascade="all, delete-orphan"
    )

    @property
    def avg_rating(self) -> float:
        if self.rating_count == 0:
            return 0.0
        return round(self.rating_sum / self.rating_count, 1)


class CapabilityArtifact(Base):
    """能力包工件（本地文件或 MinIO 对象）。"""

    __tablename__ = "capability_artifacts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    capability_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("capabilities.id"), index=True
    )
    storage_type: Mapped[str] = mapped_column(String(16), default="local")  # local | minio
    uri: Mapped[str] = mapped_column(String(512), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), default="")
    checksum: Mapped[str] = mapped_column(String(128), default="")
    size_bytes: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    capability: Mapped["Capability"] = relationship(back_populates="artifacts")


class Review(Base):
    """审核记录。latest 用于判断当前处于待审 / 审核中。"""

    __tablename__ = "reviews"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    capability_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("capabilities.id"), index=True
    )
    reviewer_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    action: Mapped[str] = mapped_column(String(20), nullable=False)
    # submitted(待审) | reviewing(审核中) | approved | rejected | returned
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    capability: Mapped["Capability"] = relationship(back_populates="reviews")


class Rating(Base):
    __tablename__ = "ratings"
    __table_args__ = (UniqueConstraint("capability_id", "user_id", name="uq_rating_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    capability_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("capabilities.id"), index=True
    )
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"))
    score: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    capability: Mapped["Capability"] = relationship(back_populates="ratings")


class Subscription(Base):
    """订阅逻辑能力（按 name 订阅），发布新版本时通知。"""

    __tablename__ = "subscriptions"
    __table_args__ = (UniqueConstraint("user_id", "capability_name", name="uq_subscription"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    capability_name: Mapped[str] = mapped_column(String(255), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class UsageEvent(Base):
    """调用/使用记录：实例化 Agent、调用工具、激活技能、安装 MCP 等。"""

    __tablename__ = "usage_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    capability_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("capabilities.id"), index=True
    )
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    # instantiate | invoke | activate | install | discover | fork
    params: Mapped[dict] = mapped_column(JSON, default=dict)
    result_status: Mapped[str] = mapped_column(String(16), default="ok")
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), index=True)


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    body: Mapped[str] = mapped_column(Text, default="")
    read: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class A2ATask(Base):
    """A2A 协议任务：Agent 之间的互调记录。"""

    __tablename__ = "a2a_tasks"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("capabilities.id"), index=True
    )
    state: Mapped[str] = mapped_column(String(20), default="submitted", index=True)
    # submitted | working | completed | failed | canceled | rejected
    message: Mapped[dict] = mapped_column(JSON, default=dict)  # 输入消息
    output_message: Mapped[dict] = mapped_column(JSON, default=dict)  # 输出消息
    artifacts: Mapped[list] = mapped_column(JSON, default=list)
    history: Mapped[list] = mapped_column(JSON, default=list)
    task_metadata: Mapped[dict] = mapped_column(JSON, default=dict)
    client_task_id: Mapped[str] = mapped_column(String(128), default="")
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class WorkflowExecution(Base):
    """工作流执行实例：记录一次 workflow 能力调用的状态、节点结果与输出。"""

    __tablename__ = "workflow_executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    workflow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("capabilities.id"), index=True
    )
    state: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    # pending | running | succeeded | failed | canceled
    input_data: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=MutableDict)
    outputs: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=MutableDict)
    node_states: Mapped[dict] = mapped_column(MutableDict.as_mutable(JSON), default=MutableDict)
    error: Mapped[str] = mapped_column(Text, default="")
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class AgentBinding(Base):
    """Agent 动态绑定：给 agent 人设挂载工具/技能/MCP（运行时组装，不生成能力包）。"""

    __tablename__ = "agent_bindings"
    __table_args__ = (UniqueConstraint("agent_id", "name", name="uq_binding_agent_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    agent_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("capabilities.id"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[str] = mapped_column(String(50), default="0.1.0")
    dependencies: Mapped[list] = mapped_column(
        MutableList.as_mutable(JSON), default=MutableList
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_by: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class MCPGatewayServer(Base):
    """MCP HTTP 中转网关注册表：把 stdio / HTTP / SSE 的 MCP 服务统一暴露为 HTTP 端点。"""

    __tablename__ = "mcp_gateway_servers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(100), unique=True, index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    transport: Mapped[str] = mapped_column(String(20), default="stdio")
    # stdio | http(streamable_http) | sse
    url: Mapped[str] = mapped_column(String(512), default="")
    headers: Mapped[dict] = mapped_column(JSON, default=dict)
    command: Mapped[str] = mapped_column(String(255), default="")
    args: Mapped[list] = mapped_column(JSON, default=list)
    env: Mapped[dict] = mapped_column(JSON, default=dict)
    cwd: Mapped[str] = mapped_column(String(512), default="")
    api_token: Mapped[str] = mapped_column(String(255), default="")
    # 外部调用该网关端点所需的令牌；留空表示内部免鉴权
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class UserCapability(Base):
    """用户从市场加入的能力（「我的能力」集合）。"""

    __tablename__ = "user_capabilities"
    __table_args__ = (UniqueConstraint("user_id", "capability_id", name="uq_user_capability"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    capability_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("capabilities.id"), index=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    capability: Mapped["Capability"] = relationship()
