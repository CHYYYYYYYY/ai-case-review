"""配置加载中心.

从 config/{env}.yaml 读取配置, 敏感值(LLM Key / DB 密码)从环境变量读.
本地与生产共用同一套 LLM 配置, 仅基础设施连接串不同.
"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

_PROJECT_ROOT = Path(__file__).resolve().parents[2]
_CONFIG_DIR = _PROJECT_ROOT / "config"

# 启动时加载 .env (开发期; 生产由容器注入环境变量)
load_dotenv(_PROJECT_ROOT / ".env")


class LLMProviderConfig(BaseModel):
    base_url: str
    api_key_env: str
    qps: int = 5
    timeout: int = 180  # 兜底 180s; manifest OCR 等大图场景可酌情调大


class LLMConfig(BaseModel):
    default_model: str = "qwen3-vl"
    default_provider: str = "qwen"
    mock: bool = False
    allow_empty_api_key: bool = False
    providers: dict[str, LLMProviderConfig]
    retry_max: int = 3
    retry_backoff_base: int = 2
    temperature: float = 0.1

    def get_api_key(self, provider: str = "qwen") -> str | None:
        """从环境变量读取 API Key, 严禁硬编码."""
        cfg = self.providers.get(provider)
        if not cfg:
            return None
        return os.environ.get(cfg.api_key_env)

    def resolve_provider(self) -> str:
        """环境变量 LLM_PROVIDER 优先, 否则用 default_provider."""
        return os.environ.get("LLM_PROVIDER", self.default_provider)


class WorkerConfig(BaseModel):
    concurrency: int = 2
    task_soft_time_limit: int = 600
    task_time_limit: int = 720


class DatabaseConfig(BaseModel):
    host_env: str = "POSTGRES_HOST"
    port_env: str = "POSTGRES_PORT"
    db_env: str = "POSTGRES_DB"
    user_env: str = "POSTGRES_USER"
    password_env: str = "POSTGRES_PASSWORD"
    pool_size: int = 5

    def dsn(self) -> str:
        """构造 SQLAlchemy async DSN."""
        host = os.environ.get(self.host_env, "localhost")
        port = os.environ.get(self.port_env, "5432")
        db = os.environ.get(self.db_env, "audit")
        user = os.environ.get(self.user_env, "audit")
        pwd = os.environ.get(self.password_env, "")
        return f"postgresql+asyncpg://{user}:{pwd}@{host}:{port}/{db}"


class RedisConfig(BaseModel):
    host_env: str = "REDIS_HOST"
    port_env: str = "REDIS_PORT"
    password_env: str = "REDIS_PASSWORD"
    queue_name: str = "audit"

    def url(self) -> str:
        host = os.environ.get(self.host_env, "localhost")
        port = os.environ.get(self.port_env, "6379")
        password = os.environ.get(self.password_env, "")
        if password:
            return f"redis://:{password}@{host}:{port}/0"
        return f"redis://{host}:{port}/0"


class StorageConfig(BaseModel):
    backend: str = "minio"   # minio | local
    local_dir_env: str = "LOCAL_STORAGE_DIR"
    endpoint_env: str = "MINIO_ENDPOINT"
    access_key_env: str = "MINIO_ACCESS_KEY"
    secret_key_env: str = "MINIO_SECRET_KEY"
    bucket_env: str = "MINIO_BUCKET_DEV"
    secure: bool = False

    def local_dir(self) -> Path:
        return Path(os.environ.get(self.local_dir_env, "data/uploads"))

    def endpoint(self) -> str:
        return os.environ.get(self.endpoint_env, "localhost:9000")

    def access_key(self) -> str:
        return os.environ.get(self.access_key_env, "")

    def secret_key(self) -> str:
        return os.environ.get(self.secret_key_env, "")

    def bucket(self) -> str:
        return os.environ.get(self.bucket_env, "audit-dev")


class UploadConfig(BaseModel):
    max_photo_count: int = 30
    max_file_size_mb: int = 10
    allowed_types: list[str] = Field(default_factory=lambda: ["image/jpeg", "image/png", "image/webp"])


class WebhookConfig(BaseModel):
    max_retries: int = 3
    backoff_seconds: list[int] = Field(default_factory=lambda: [10, 60, 300])


class TaskConfig(BaseModel):
    retain_days: int = 7


class AppConfig(BaseModel):
    env: str = "development"
    log_level: str = "INFO"
    llm: LLMConfig
    worker: WorkerConfig = WorkerConfig()
    database: DatabaseConfig = DatabaseConfig()
    redis: RedisConfig = RedisConfig()
    storage: StorageConfig = StorageConfig()
    upload: UploadConfig = UploadConfig()
    webhook: WebhookConfig = WebhookConfig()
    task: TaskConfig = TaskConfig()


def _load_raw(env: str | None = None) -> dict[str, Any]:
    """读取 yaml 配置文件."""
    env = env or os.environ.get("APP_ENV", "development")
    path = _CONFIG_DIR / f"{env}.yaml"
    if not path.exists():
        # 回退到 development
        path = _CONFIG_DIR / "development.yaml"
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def _build_config(raw: dict[str, Any]) -> AppConfig:
    """把 yaml 字典构造为强类型配置对象."""
    llm_raw = raw.get("llm", {})
    providers = {
        name: LLMProviderConfig(**cfg)
        for name, cfg in llm_raw.get("providers", {}).items()
    }
    retry = llm_raw.get("retry", {})
    llm = LLMConfig(
        default_model=llm_raw.get("default_model", "qwen3-vl"),
        default_provider=llm_raw.get("default_provider", "qwen"),
        mock=llm_raw.get("mock", False),
        allow_empty_api_key=llm_raw.get("allow_empty_api_key", False),
        providers=providers,
        retry_max=retry.get("max_retries", 3),
        retry_backoff_base=retry.get("backoff_base", 2),
        temperature=llm_raw.get("temperature", 0.1),
    )
    storage_raw = raw.get("storage", {})
    storage = StorageConfig(
        backend=storage_raw.get("backend", "minio"),
        local_dir_env=storage_raw.get("local_dir_env", "LOCAL_STORAGE_DIR"),
        endpoint_env=storage_raw.get("endpoint_env", "MINIO_ENDPOINT"),
        access_key_env=storage_raw.get("access_key_env", "MINIO_ACCESS_KEY"),
        secret_key_env=storage_raw.get("secret_key_env", "MINIO_SECRET_KEY"),
        bucket_env=storage_raw.get("bucket_env", "MINIO_BUCKET_DEV"),
        secure=storage_raw.get("secure", False),
    )
    return AppConfig(
        env=raw.get("app", {}).get("env", "development"),
        log_level=raw.get("app", {}).get("log_level", "INFO"),
        llm=llm,
        worker=WorkerConfig(**raw.get("worker", {})),
        database=DatabaseConfig(**raw.get("database", {})),
        redis=RedisConfig(**raw.get("redis", {})),
        storage=storage,
        upload=UploadConfig(**raw.get("upload", {})),
        webhook=WebhookConfig(**raw.get("webhook", {})),
        task=TaskConfig(**raw.get("task", {})),
    )


@lru_cache(maxsize=1)
def get_config() -> AppConfig:
    """全局配置单例."""
    return _build_config(_load_raw())
