"""应用配置：通过环境变量覆盖，默认开箱即用（SQLite + 本地存储 + 内存缓存）。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.core.env_guard import require_secret


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "AI 能力公共市场平台"
    app_version: str = "0.1.0"
    debug: bool = False

    # 数据库
    database_url: str = "sqlite+aiosqlite:///./data/marketplace.db"

    # 能力包存储
    artifact_storage: str = "local"  # local | minio
    artifact_dir: str = "./data/artifacts"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "minioadmin"
    minio_secret_key: str = "minioadmin"
    minio_bucket: str = "marketplace"
    minio_secure: bool = False

    # 安全
    jwt_secret: str = "dev-secret-change-me-please-32-bytes-minimum"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720  # 本地会话 12h(默认),可用 JWT_EXPIRE_MINUTES 覆盖
    # 逗号分隔；生产请配置实际前端源。空则仅允许本机 Vite。
    cors_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    # MCP 网关：True 时 api_token 为空则拒绝外部调用（强制配置令牌）
    mcp_gateway_require_token: bool = False

    # 种子数据
    seed_admin_username: str = "admin"
    seed_admin_password: str = "admin123"
    seed_admin_email: str = "admin@example.com"
    seed_publisher_password: str = "change-me"
    seed_user_password: str = "change-me"

    # 能力包上传限制
    max_artifact_size_mb: int = 50

    # 工具沙箱
    tool_timeout_seconds: int = 15
    tool_max_output_bytes: int = 2 * 1024 * 1024
    tool_max_extract_bytes: int = 256 * 1024 * 1024

    # Agent 真实执行（OpenAI 兼容网关）
    llm_base_url: str = ""
    llm_api_key: str = ""
    llm_model: str = ""
    agent_max_iterations: int = 10

    # runtime 调用授权：只有这些角色可以调用/执行能力（工具调用、Agent 任务、技能激活、
    # MCP 安装、工作流执行、A2A 委派）。默认仅管理员，可配成 "admin,publisher" 等。
    runtime_access_roles: str = "admin"

    # SSO/OIDC 可选接入:配置 sso_issuer 后认证认 SSO token(D-ready)
    sso_issuer: str = ""
    sso_audience: str = ""
    sso_jwks_uri: str = ""
    sso_client_id: str = "market"
    sso_client_secret: str = ""
    sso_redirect_uri: str = ""
    # 回调成功后浏览器落地页(可相对路径或绝对 URL)
    sso_redirect_target: str = "/login"

    @property
    def base_dir(self) -> Path:
        """后端项目根目录（backend/）。"""
        return Path(__file__).resolve().parent.parent

    @property
    def data_dir(self) -> Path:
        return self.base_dir / "data"

    @property
    def artifact_path(self) -> Path:
        p = Path(self.artifact_dir)
        if not p.is_absolute():
            p = self.base_dir / p
        return p.resolve()

    def resolved_database_url(self) -> str:
        if self.database_url.startswith("sqlite") and "///" in self.database_url:
            scheme, raw = self.database_url.split("///", 1)
            if raw not in (":memory:", "") and not Path(raw).is_absolute():
                return f"{scheme}///{(self.base_dir / raw).as_posix()}"
        return self.database_url


@lru_cache
def get_settings() -> Settings:
    settings = Settings()
    # 启动即校验:生产环境缺失或仍为弱值则抛错,开发环境告警放行。
    require_secret("JWT_SECRET", settings.jwt_secret)
    require_secret("SEED_ADMIN_PASSWORD", settings.seed_admin_password)
    require_secret("SEED_PUBLISHER_PASSWORD", settings.seed_publisher_password)
    require_secret("SEED_USER_PASSWORD", settings.seed_user_password)
    # 确保数据目录存在
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.artifact_path.mkdir(parents=True, exist_ok=True)
    return settings


# 导入 app.config 即触发一次校验,保证生产环境在启动最早期失败。
get_settings()
