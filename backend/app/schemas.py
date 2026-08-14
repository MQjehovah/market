"""Pydantic 请求/响应模型。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CAPABILITY_TYPES = ("agent", "tool", "skill", "mcp", "workflow")
VISIBILITY_LEVELS = ("private", "team", "internal", "public")
STATUS_LEVELS = ("draft", "reviewing", "published", "deprecated", "archived", "rejected", "returned")
ROLES = ("admin", "publisher", "user")


class UserRegister(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[\w.\-]+$")
    email: str = Field(max_length=255)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=64)
    organization: str = Field(default="", max_length=100)
    team: str = Field(default="", max_length=100)


class UserLogin(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    username: str
    email: str
    display_name: str
    role: str
    organization: str
    team: str
    created_at: datetime


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class CapabilityBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=20000)
    type: Literal["agent", "tool", "skill", "mcp", "workflow"]
    version: str = Field(default="0.1.0", max_length=50)
    category: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list)
    visibility: Literal["private", "team", "internal", "public"] = "internal"

    @field_validator("version")
    @classmethod
    def validate_semver(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError("版本号必须符合语义化版本 MAJOR.MINOR.PATCH，如 1.2.3")
        return v

    @field_validator("tags")
    @classmethod
    def validate_tags(cls, v: list[str]) -> list[str]:
        return [t.strip()[:32] for t in v if t.strip()][:20]


class CapabilityCreate(CapabilityBase):
    pass


class CapabilityUpdate(BaseModel):
    description: str | None = None
    category: str | None = Field(default=None, max_length=100)
    tags: list[str] | None = None
    visibility: Literal["private", "team", "internal", "public"] | None = None


class VersionCreate(BaseModel):
    new_version: str = Field(max_length=50)
    change_type: Literal["major", "minor", "patch"] = "patch"

    @field_validator("new_version")
    @classmethod
    def validate_semver(cls, v: str) -> str:
        parts = v.split(".")
        if len(parts) != 3 or not all(p.isdigit() for p in parts):
            raise ValueError("版本号必须符合语义化版本 MAJOR.MINOR.PATCH")
        return v


class ArtifactOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    filename: str
    storage_type: str
    checksum: str
    size_bytes: int
    uploaded_at: datetime


class ReviewOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    action: str
    comment: str
    reviewer_id: str
    created_at: datetime


class RatingCreate(BaseModel):
    score: int = Field(ge=1, le=5)
    comment: str = Field(default="", max_length=2000)


class RatingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    username: str = ""
    score: int
    comment: str
    created_at: datetime


class CapabilityOut(CapabilityBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    author_id: str
    author_name: str = ""
    organization: str
    input_schema: dict[str, Any] = Field(default_factory=dict)
    usage_count: int
    rating_sum: float
    rating_count: int
    avg_rating: float = 0.0
    created_at: datetime
    updated_at: datetime
    artifacts: list[ArtifactOut] = Field(default_factory=list)
    latest: bool = False


class CapabilityPage(BaseModel):
    items: list[CapabilityOut]
    total: int
    page: int
    page_size: int


class ReviewRequest(BaseModel):
    action: Literal["approve", "reject", "return", "take"]
    comment: str = Field(default="", max_length=2000)


class SubscribeRequest(BaseModel):
    capability_name: str = Field(min_length=1, max_length=255)


class NotificationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    body: str
    read: bool
    created_at: datetime


class StatsOut(BaseModel):
    total_capabilities: int
    published_count: int
    reviewing_count: int
    total_usage: int
    total_users: int
    type_breakdown: dict[str, int]
    top_used: list[dict[str, Any]]
    recent_usage: list[dict[str, Any]]


class UsageEventOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    user_id: str
    capability_id: str
    action: str
    params: dict
    result_status: str
    created_at: datetime


class RuntimeResult(BaseModel):
    ok: bool = True
    capability: CapabilityOut
    action: str
    message: str
    result: dict[str, Any] = Field(default_factory=dict)


class RuntimeInvokeRequest(BaseModel):
    params: dict[str, Any] = Field(default_factory=dict)


class RuntimeActivateRequest(BaseModel):
    context: str = ""


class RuntimeInstallRequest(BaseModel):
    config: dict[str, Any] = Field(default_factory=dict)


class AssembleDependency(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: Literal["tool", "skill", "mcp"]
    version: str = Field(default="", max_length=50, description="留空取最新发布版")


class AssembleRequest(BaseModel):
    persona: str = Field(min_length=1, max_length=255, description="作为人设的 agent 能力名")
    persona_version: str = Field(default="", max_length=50)
    name: str = Field(min_length=1, max_length=255, description="组装后 agent 的名称")
    version: str = Field(default="0.1.0", max_length=50)
    description: str = Field(default="", max_length=20000)
    category: str = Field(default="组装", max_length=100)
    tags: list[str] = Field(default_factory=list)
    dependencies: list[AssembleDependency] = Field(default_factory=list)


class AgentBindingCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=20000)
    version: str = Field(default="0.1.0", max_length=50)
    dependencies: list[AssembleDependency] = Field(default_factory=list)
    enabled: bool = True


class AgentBindingUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    description: str | None = None
    version: str | None = None
    dependencies: list[AssembleDependency] | None = None
    enabled: bool | None = None


class AgentBindingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    agent_id: str
    agent_name: str = ""
    name: str
    description: str = ""
    version: str
    dependencies: list[dict[str, str]]
    enabled: bool
    created_by: str
    created_at: datetime
    updated_at: datetime


class WorkflowCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=20000)
    version: str = Field(default="0.1.0", max_length=50)
    category: str = Field(default="工作流", max_length=100)
    tags: list[str] = Field(default_factory=list)
    visibility: Literal["private", "team", "internal", "public"] = "internal"
    workflow: dict[str, Any] = Field(description="workflow.json 内容：nodes + edges")


class WorkflowExecuteRequest(BaseModel):
    input: dict[str, Any] = Field(default_factory=dict)


class RuntimeInstantiateRequest(BaseModel):
    task: str = "执行任务"
    binding: str = Field(default="", description="绑定名称或 ID，留空用默认绑定")
    bindings: list[AssembleDependency] = Field(default_factory=list, description="临时绑定，不落库")


class RuntimeTaskRequest(BaseModel):
    task: str = Field(min_length=1, max_length=20000)


class RuntimeTaskOut(BaseModel):
    task_id: str
    agent: str
    version: str
    mode: str
    output: str
    tool_calls: int = 0
    runtime: dict[str, Any] = Field(default_factory=dict)


class AgentEditOut(BaseModel):
    capability: CapabilityOut
    prompt: str = ""
    dependencies: list[dict[str, str]] = Field(default_factory=list)
    base_version: str = ""


class AgentEditSave(BaseModel):
    prompt: str = Field(default="", max_length=200000)
    description: str = Field(default="", max_length=20000)
    category: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list)
    new_version: str = Field(default="", max_length=50, description="留空则基于最新版本 patch+1")
    dependencies: list[AssembleDependency] = Field(default_factory=list)


class WorkflowExecutionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    workflow_id: str
    workflow_name: str = ""
    state: str
    input_data: dict[str, Any]
    outputs: dict[str, Any]
    node_states: dict[str, Any]
    error: str = ""
    created_by: str
    created_at: datetime
    updated_at: datetime


class MessageOut(BaseModel):
    message: str
