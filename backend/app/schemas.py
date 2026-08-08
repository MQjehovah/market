"""Pydantic 请求/响应模型。"""

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

CAPABILITY_TYPES = ("agent", "tool", "skill", "mcp")
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
    type: Literal["agent", "tool", "skill", "mcp"]
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
    usage_count: int
    rating_sum: float
    rating_count: int
    avg_rating: float = 0.0
    created_at: datetime
    updated_at: datetime
    artifacts: list[ArtifactOut] = Field(default_factory=list, exclude=True)
    latest: bool = False


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


class MessageOut(BaseModel):
    message: str
