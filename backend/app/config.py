"""应用配置：通过环境变量覆盖，默认开箱即用（SQLite + 本地存储 + 内存缓存）。"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # 缓存（内存 TTL，单进程部署开箱即用；多实例部署建议接入分布式缓存）
    cache_enabled: bool = True
    cache_ttl: int = 300

    # 安全
    jwt_secret: str = "dev-secret-change-me-please-32-bytes-minimum"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60 * 24

    # 种子数据
    seed_admin_username: str = "admin"
    seed_admin_password: str = "admin123"
    seed_admin_email: str = "admin@example.com"

    # 能力包上传限制
    max_artifact_size_mb: int = 50

    # 工具沙箱
    tool_timeout_seconds: int = 15
    tool_max_output_bytes: int = 2 * 1024 * 1024
    tool_max_extract_bytes: int = 256 * 1024 * 1024

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
    # 确保数据目录存在
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    settings.artifact_path.mkdir(parents=True, exist_ok=True)
    return settings
