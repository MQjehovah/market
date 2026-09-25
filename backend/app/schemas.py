"""Pydantic 请求/响应模型。"""

import re
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# 标准机器名(slug)允许字符: MCP 命名空间(io.github.user/server-name) 与 Skill 名(pdf-processing)
_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._/-]*$")

CAPABILITY_TYPES = (
    "agent",
    "tool",
    "skill",
    "mcp",
    "workflow",
    "plugin",
    "rule",
    "command",
    "hook",
)
CapabilityType = Literal[
    "agent", "tool", "skill", "mcp", "workflow", "plugin", "rule", "command", "hook"
]
VISIBILITY_LEVELS = ("private", "team", "internal", "public")
STATUS_LEVELS = ("draft", "reviewing", "published", "deprecated", "archived", "rejected", "returned")
ROLES = ("admin", "publisher", "user")


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
    department: str = ""
    is_active: bool = True
    created_at: datetime


class UserAdminCreate(BaseModel):
    username: str = Field(min_length=3, max_length=64, pattern=r"^[\w.\-]+$")
    email: str = Field(max_length=255)
    password: str = Field(min_length=6, max_length=128)
    display_name: str = Field(default="", max_length=64)
    organization: str = Field(default="", max_length=100)
    team: str = Field(default="", max_length=100)
    department: str = Field(default="", max_length=100)
    role: Literal["admin", "publisher", "user"] = "user"


class UserAdminUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=3, max_length=64, pattern=r"^[\w.\-]+$")
    email: str | None = Field(default=None, max_length=255)
    password: str | None = Field(default=None, min_length=6, max_length=128, description="留空不修改")
    display_name: str | None = Field(default=None, max_length=64)
    organization: str | None = Field(default=None, max_length=100)
    team: str | None = Field(default=None, max_length=100)
    department: str | None = Field(default=None, max_length=100)
    role: Literal["admin", "publisher", "user"] | None = None
    is_active: bool | None = None


class ChangePasswordRequest(BaseModel):
    old_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=6, max_length=128)


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut


class CapabilityBase(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = Field(default="", max_length=20000)
    display_name: str = Field(default="", max_length=255)
    slug: str = Field(default="", max_length=255, description="标准机器名(MCP server.json.name / Skill name)")
    type: CapabilityType
    version: str = Field(default="0.1.0", max_length=50)
    category: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list)
    visibility: Literal["private", "team", "internal", "public"] = "internal"
    access_policy: Literal["open", "admin_only", "restricted"] = "open"
    allowed_users: list[str] = Field(default_factory=list, description="restricted 时的白名单用户名")
    install_policy: Literal["optional", "default_on", "required"] = "optional"
    distribution: Literal["local", "remote", "both"] = "both"
    binding: Literal["user", "service"] = "service"
    risk_default: Literal["read", "write", "destructive"] = "read"
    data_domain: str = Field(default="", max_length=64)

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

    @field_validator("data_domain")
    @classmethod
    def validate_data_domain(cls, v: str) -> str:
        return (v or "").strip()[:64]

    @field_validator("slug")
    @classmethod
    def validate_slug(cls, v: str) -> str:
        """标准机器名: 空允许; 否则须匹配行业命名(MCP 命名空间 / Skill 小写连字符)。"""
        value = (v or "").strip()
        if not value:
            return ""
        if len(value) > 255 or not _SLUG_RE.match(value) or value.endswith(("/", ".", "-")):
            raise ValueError(
                "slug 须为小写字母/数字/./_/- 且以字母或数字开头(MCP 命名空间或 Skill 名), 如 io.github.x/y 或 pdf-processing"
            )
        return value


class CapabilityCreate(CapabilityBase):
    pass


class CapabilityUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    type: CapabilityType | None = None
    description: str | None = None
    display_name: str | None = Field(default=None, max_length=255)
    slug: str | None = Field(default=None, max_length=255)
    category: str | None = Field(default=None, max_length=100)
    tags: list[str] | None = None
    visibility: Literal["private", "team", "internal", "public"] | None = None
    access_policy: Literal["open", "admin_only", "restricted"] | None = None
    allowed_users: list[str] | None = None
    install_policy: Literal["optional", "default_on", "required"] | None = None
    distribution: Literal["local", "remote", "both"] | None = None
    binding: Literal["user", "service"] | None = None
    risk_default: Literal["read", "write", "destructive"] | None = None
    data_domain: str | None = Field(default=None, max_length=64)

    @field_validator("slug")
    @classmethod
    def validate_slug_opt(cls, v: str | None) -> str | None:
        if v is None:
            return None
        return CapabilityBase.validate_slug(v)


class InstallPolicyUpdate(BaseModel):
    install_policy: Literal["optional", "default_on", "required"]


class BindingUpdate(BaseModel):
    """执行身份绑定：user=按提问者代授权(subject) / service=服务身份。"""

    binding: Literal["user", "service"]


class VersionCreate(BaseModel):
    new_version: str | None = Field(default=None, max_length=50)
    change_type: Literal["major", "minor", "patch"] = "patch"
    changelog: str = Field(default="", max_length=5000)

    @field_validator("new_version")
    @classmethod
    def validate_semver(cls, v: str | None) -> str | None:
        if v is None or v == "":
            return None
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


class RuntimeSpecOut(BaseModel):
    """能力运行规格（设计文档 §3）：两端只消费、不各自推导。"""

    cloud: bool = True
    local: bool = True
    recommended: str = "cloud"
    transport: str = ""
    command: str = ""
    args: list[str] = Field(default_factory=list)
    url: str = ""
    env: dict[str, str] = Field(default_factory=dict)
    tool_count: int = 0
    risk: str = "read"
    dependencies: list[dict[str, str]] = Field(default_factory=list)


class CapabilityOut(CapabilityBase):
    model_config = ConfigDict(from_attributes=True)

    id: str
    status: str
    author_id: str
    author_name: str = ""
    organization: str
    changelog: str = ""
    readme_md: str = ""
    provenance: dict[str, Any] = Field(default_factory=dict)
    icon_url: str = ""
    runtime: RuntimeSpecOut = Field(default_factory=RuntimeSpecOut)
    input_schema: dict[str, Any] = Field(default_factory=dict)
    validation_report: dict[str, Any] = Field(default_factory=dict)
    allowed_departments: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    usage_count: int
    rating_sum: float
    rating_count: int
    avg_rating: float = 0.0
    created_at: datetime
    updated_at: datetime
    artifacts: list[ArtifactOut] = Field(default_factory=list)
    latest: bool = False
    # skill/mcp：被哪些 agent/plugin 引用；plugin 子能力：父插件 id
    used_by: list[dict[str, Any]] = Field(default_factory=list)
    parent_plugin_id: str | None = None
    # 桌面消费投影；plugin 的已发布子能力（与 input_schema.components 同源）
    consumers: dict[str, Any] = Field(default_factory=dict)
    components: list[dict[str, Any]] = Field(default_factory=list)


class CapabilityPage(BaseModel):
    items: list[CapabilityOut]
    total: int
    page: int
    page_size: int


class TagsMetaOut(BaseModel):
    """标签聚合（浏览页筛选 chips）：Top N，过滤 plugin-component。"""

    tags: list[str] = Field(default_factory=list)


class TaskSearchHitOut(CapabilityOut):
    """任务搜索单条：在 CapabilityOut 上附带打分与命中词。"""

    match_score: float = 0.0
    matched_terms: list[str] = Field(default_factory=list)


class TaskSearchOut(BaseModel):
    """按要办的事搜索：分组结果。"""

    q: str = ""
    terms: list[str] = Field(default_factory=list)
    agents: list[TaskSearchHitOut] = Field(default_factory=list)
    skills: list[TaskSearchHitOut] = Field(default_factory=list)
    mcps: list[TaskSearchHitOut] = Field(default_factory=list)
    plugins: list[TaskSearchHitOut] = Field(default_factory=list)
    others: list[TaskSearchHitOut] = Field(default_factory=list)


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
    link: str = ""
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
    capability_version: str = ""
    action: str
    params: dict
    result_status: str
    duration_ms: int = 0
    conversation_id: str = ""
    source: str = "platform"
    created_at: datetime


class UserSecretUpsert(BaseModel):
    key_name: str = Field(min_length=1, max_length=128)
    value: str = Field(min_length=1, max_length=8192)
    # 空=全局；填 capability_id 为该能力专用覆盖
    scope: str = Field(default="", max_length=36)
    label: str = Field(default="", max_length=128)


class UserSecretBulkUpsert(BaseModel):
    secrets: dict[str, str] = Field(default_factory=dict, description="key_name → 明文值")
    scope: str = Field(default="", max_length=36, description="空=全局；或 capability_id")


class UserSecretOut(BaseModel):
    id: str
    key_name: str
    scope: str = ""
    label: str = ""
    has_value: bool = True
    updated_at: datetime | None = None
    created_at: datetime | None = None


class UserSecretStatusOut(BaseModel):
    required: list[str] = Field(default_factory=list)
    filled: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    complete: bool = False


class CapabilitySecretUpsert(BaseModel):
    secrets: dict[str, str] = Field(default_factory=dict, description="key_name → 明文值，空值跳过")


class CapabilitySecretItemOut(BaseModel):
    key_name: str
    updated_at: datetime | None = None
    updated_by: str = ""


class CapabilitySecretsOut(BaseModel):
    """平台密钥元数据（不含明文）+ 能力声明的 env 键。"""

    items: list[CapabilitySecretItemOut] = Field(default_factory=list)
    declared_env: list[str] = Field(default_factory=list)


class ServiceTokenCreate(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    scopes: list[str] = Field(default_factory=lambda: ["runtime", "gateway", "sync"])
    expires_days: int | None = Field(default=365, ge=1, le=3650)
    # 绑定已有用户；留空则自动创建 svc_<slug> 服务账号
    user_id: str | None = None


class ServiceTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    token_prefix: str
    user_id: str
    username: str = ""
    scopes: list[str] = Field(default_factory=list)
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked: bool = False
    created_by: str
    created_at: datetime


class ServiceTokenCreated(ServiceTokenOut):
    """创建时一次性返回明文 token。"""

    token: str


class AccessPolicyUpdate(BaseModel):
    access_policy: Literal["open", "admin_only", "restricted"] = "open"
    allowed_users: list[str] = Field(default_factory=list, description="restricted 时的白名单用户名")
    allowed_departments: list[str] | None = Field(default=None, description="部门白名单；None 表示保持原值，[] 表示清空")
    allowed_roles: list[str] | None = Field(default=None, description="角色白名单；None 表示保持原值，[] 表示清空")


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


class McpCallRequest(BaseModel):
    tool: str = Field(min_length=1, max_length=200, description="MCP 暴露的工具名（mcp_<服务>_<工具>）")
    params: dict[str, Any] = Field(default_factory=dict)


class AssembleDependency(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    type: Literal["tool", "skill", "mcp", "rule", "command", "hook"]
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
    category: str = Field(default="能力编排", max_length=100)
    tags: list[str] = Field(default_factory=list)
    visibility: Literal["private", "team", "internal", "public"] = "internal"
    access_policy: Literal["open", "admin_only", "restricted"] = "open"
    allowed_users: list[str] = Field(default_factory=list, description="restricted 时的白名单用户名")
    workflow: dict[str, Any] = Field(description="workflow.json 内容：nodes + edges")


class WorkflowUpdate(BaseModel):
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
    steps: list[dict[str, Any]] = Field(default_factory=list, description="执行步骤轨迹（LLM/工具/技能/连接器调用）")


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


class SkillEditOut(BaseModel):
    capability: CapabilityOut
    skill_md: str = ""
    files: list[dict[str, Any]] = Field(default_factory=list)
    base_version: str = ""


class SkillEditSave(BaseModel):
    skill_md: str = Field(default="", max_length=500000)
    description: str = Field(default="", max_length=20000)
    category: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list)
    new_version: str = Field(default="", max_length=50, description="留空则基于最新版本 patch+1")


class MarkdownKindEditOut(BaseModel):
    capability: CapabilityOut
    body: str = ""
    files: list[dict[str, Any]] = Field(default_factory=list)
    base_version: str = ""
    always_apply: bool = False
    globs: str = ""


class MarkdownKindEditSave(BaseModel):
    body: str = Field(default="", max_length=500000)
    description: str = Field(default="", max_length=20000)
    category: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list)
    new_version: str = Field(default="", max_length=50, description="留空则基于最新版本 patch+1")
    always_apply: bool | None = None
    globs: str = Field(default="", max_length=500)


class HookEditOut(BaseModel):
    capability: CapabilityOut
    hooks_json: str = ""
    files: list[dict[str, Any]] = Field(default_factory=list)
    base_version: str = ""


class HookEditSave(BaseModel):
    hooks_json: str = Field(default="", max_length=500000)
    description: str = Field(default="", max_length=20000)
    category: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list)
    new_version: str = Field(default="", max_length=50, description="留空则基于最新版本 patch+1")


class ToolEditOut(BaseModel):
    capability: CapabilityOut
    tool_schema: dict[str, Any] = Field(default_factory=dict)
    implementation: str = ""
    files: list[dict[str, Any]] = Field(default_factory=list)
    base_version: str = ""


class ToolEditSave(BaseModel):
    tool_schema: dict[str, Any] = Field(default_factory=dict)
    implementation: str = Field(default="", max_length=500000)
    description: str = Field(default="", max_length=20000)
    category: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list)
    new_version: str = Field(default="", max_length=50, description="留空则基于最新版本 patch+1")


class McpImplementationFile(BaseModel):
    path: str = Field(max_length=255, description="相对路径，须为 implementation/*.py")
    content: str = Field(default="", max_length=500000)


class McpEditOut(BaseModel):
    capability: CapabilityOut
    connection: dict[str, Any] = Field(default_factory=dict)
    tools_json: Any = None
    implementations: list[McpImplementationFile] = Field(default_factory=list)
    files: list[dict[str, Any]] = Field(default_factory=list)
    base_version: str = ""


class McpEditSave(BaseModel):
    connection: dict[str, Any] = Field(default_factory=dict)
    tools_json: Any = None
    implementations: list[McpImplementationFile] | None = Field(
        default=None,
        description="为 null 时保留原 implementation/*.py；传入列表则整体替换",
    )
    description: str = Field(default="", max_length=20000)
    category: str = Field(default="", max_length=100)
    tags: list[str] = Field(default_factory=list)
    new_version: str = Field(default="", max_length=50, description="留空则基于最新版本 patch+1")


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


class MCPGatewayServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=100, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    description: str = Field(default="", max_length=2000)
    transport: Literal["stdio", "http", "sse"] = "stdio"
    url: str = Field(default="", max_length=500)
    headers: dict[str, str] = Field(default_factory=dict)
    command: str = Field(default="", max_length=255)
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    cwd: str = Field(default="", max_length=500)
    api_token: str = Field(default="", max_length=255)
    capability_id: str = Field(default="", max_length=36, description="关联的市场 mcp 能力 id；空 = 未绑定")
    enabled: bool = True


class MCPGatewayServerOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    description: str = ""
    transport: str = "stdio"
    url: str = ""
    headers: dict = {}
    command: str = ""
    args: list = []
    env: dict = {}
    cwd: str = ""
    api_token: str = ""
    capability_id: str = ""
    enabled: bool = True
    created_at: datetime
    updated_at: datetime


class MCPGatewayTestOut(BaseModel):
    connected: bool
    error: str = ""
    tools: list[dict] = []


class MessageOut(BaseModel):
    message: str


class PackageFileEntry(BaseModel):
    path: str
    size: int
    text: bool = True


class PackageTreeOut(BaseModel):
    files: list[PackageFileEntry] = Field(default_factory=list)
    artifact_filename: str = ""
    artifact_size: int = 0


class PackageFileContentOut(BaseModel):
    path: str
    size: int
    truncated: bool = False
    binary: bool = False
    content: str = ""
    encoding: str = ""


class MyCapabilityAdd(BaseModel):
    capability_id: str


class MyCapabilityPatch(BaseModel):
    enabled: bool


class MySubscriptionsOut(BaseModel):
    """订阅更新清单（能力名），详情页两态按钮用。"""

    names: list[str] = Field(default_factory=list)


class HostSyncItem(BaseModel):
    """宿主（零号员工 / 桌面）应安装的一条已加入且启用能力。"""

    id: str
    name: str
    type: str
    version: str
    description: str = ""
    enabled: bool = True
    download_url: str = ""
    consumers: dict[str, Any] = Field(default_factory=dict)


class HostSyncOut(BaseModel):
    items: list[HostSyncItem] = Field(default_factory=list)
    hint: str = (
        "只含已加入且启用的项。宿主安装后应尊重 enabled："
        "停用后下次同步请忽略或卸载，不必再复制 cap install。"
    )
