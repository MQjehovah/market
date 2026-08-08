"""A2A 协议数据类型（JSON-RPC 2.0 传输，字段遵循 camelCase）。"""

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel


class CamelModel(BaseModel):
    model_config = ConfigDict(
        populate_by_name=True,
        alias_generator=to_camel,
        extra="allow",
    )


class A2APart(CamelModel):
    type: str = "text"  # text | data | file | audio | video
    text: str | None = None
    data: Any | None = None
    metadata: dict[str, Any] | None = None


class A2AMessage(CamelModel):
    role: Literal["user", "agent"]
    parts: list[A2APart] = Field(default_factory=list)


class A2AStatus(CamelModel):
    state: Literal[
        "submitted", "working", "input-required", "completed", "failed", "canceled", "rejected"
    ]
    message: A2AMessage | None = None


class A2AArtifact(CamelModel):
    name: str
    description: str | None = None
    parts: list[A2APart] = Field(default_factory=list)
    index: int = 0


class A2ATask(CamelModel):
    id: str
    status: A2AStatus
    artifacts: list[A2AArtifact] = Field(default_factory=list)
    history: list[A2AMessage] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2ATaskSendParams(CamelModel):
    id: str = Field(min_length=1, max_length=128)
    message: A2AMessage
    metadata: dict[str, Any] = Field(default_factory=dict)


class A2ATaskGetParams(CamelModel):
    id: str


class A2ATaskCancelParams(CamelModel):
    id: str
    reason: str | None = None


class JsonRpcRequest(CamelModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    method: str
    params: dict[str, Any] = Field(default_factory=dict)


class JsonRpcError(CamelModel):
    code: int
    message: str
    data: Any | None = None


class JsonRpcResponse(CamelModel):
    jsonrpc: str = "2.0"
    id: str | int | None = None
    result: Any | None = None
    error: JsonRpcError | None = None


class AgentSkill(CamelModel):
    id: str
    name: str
    description: str | None = None
    tags: list[str] = Field(default_factory=list)
    examples: list[str] = Field(default_factory=list)


class AgentCapabilities(CamelModel):
    streaming: bool = False
    push_notifications: bool = False
    state_transition_history: bool = True


class SecurityScheme(CamelModel):
    type: str = "http"
    scheme: str = "bearer"


class AgentCard(CamelModel):
    protocol_version: str = "1.0"
    name: str
    description: str
    url: str
    version: str
    provider: dict[str, Any] = Field(default_factory=dict)
    documentation_url: str | None = None
    capabilities: AgentCapabilities = Field(default_factory=AgentCapabilities)
    security_schemes: dict[str, SecurityScheme] = Field(default_factory=dict)
    security: list[dict[str, Any]] = Field(default_factory=list)
    default_input_modes: list[str] = Field(default_factory=lambda: ["text"])
    default_output_modes: list[str] = Field(default_factory=lambda: ["text"])
    skills: list[AgentSkill] = Field(default_factory=list)


def extract_text(message: A2AMessage | dict[str, Any] | None) -> str:
    """从 A2A 消息中提取纯文本，便于执行与记录。"""
    if message is None:
        return ""
    data = message if isinstance(message, dict) else message.model_dump(by_alias=True)
    texts = []
    for part in data.get("parts") or []:
        if part.get("type") == "text" and part.get("text"):
            texts.append(part["text"])
        elif part.get("text"):
            texts.append(str(part["text"]))
    return "\n".join(texts)
