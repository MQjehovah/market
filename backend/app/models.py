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
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

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
    __table_args__ = (UniqueConstraint("name", "version", name="uq_capability_name_version"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=_uuid)
    name: Mapped[str] = mapped_column(String(255), index=True, nullable=False)
    description: Mapped[str] = mapped_column(Text, default="")
    type: Mapped[str] = mapped_column(String(16), index=True, nullable=False)  # agent|tool|skill|mcp
    version: Mapped[str] = mapped_column(String(50), nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="draft", index=True)
    # draft | reviewing | published | deprecated | archived | rejected | returned
    category: Mapped[str] = mapped_column(String(100), default="", index=True)
    tags: Mapped[list] = mapped_column(JSON, default=list)
    author_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), index=True)
    organization: Mapped[str] = mapped_column(String(100), default="")
    visibility: Mapped[str] = mapped_column(String(16), default="internal")
    # private | team | internal | public
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
