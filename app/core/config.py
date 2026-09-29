"""Application core configuration."""

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Gateway application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        populate_by_name=True,
    )

    app_name: str = "Self-Healing LLM Gateway"
    app_version: str = "0.1.0"
    environment: str = Field(default="development", alias="ENVIRONMENT")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    app_host: str = Field(default="0.0.0.0", alias="APP_HOST")
    app_port: int = Field(default=8000, alias="APP_PORT")

    # Storage and Provider URLs
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    database_url: str = Field(
        default="postgresql+asyncpg://postgres:postgres@localhost:5432/llm_gateway",
        alias="DATABASE_URL",
    )
    db_pool_size: int = Field(default=10, alias="DB_POOL_SIZE")
    db_max_overflow: int = Field(default=20, alias="DB_MAX_OVERFLOW")
    db_pool_timeout: float = Field(default=30.0, alias="DB_POOL_TIMEOUT")
    llm_provider: str = Field(default="ollama", alias="LLM_PROVIDER")
    ollama_base_url: str = Field(default="http://localhost:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen2.5:3b", alias="OLLAMA_MODEL")
    provider_timeout: float = Field(default=30.0, alias="PROVIDER_TIMEOUT")

    # Reliability: Retry Configuration
    max_retries: int = Field(default=2, alias="MAX_RETRIES")
    retry_base_delay: float = Field(default=0.5, alias="RETRY_BASE_DELAY")
    retry_max_delay: float = Field(default=5.0, alias="RETRY_MAX_DELAY")
    retry_jitter: bool = Field(default=True, alias="RETRY_JITTER")

    # Reliability: Circuit Breaker Configuration
    circuit_failure_threshold: int = Field(default=5, alias="CIRCUIT_FAILURE_THRESHOLD")
    circuit_cooldown_seconds: float = Field(default=30.0, alias="CIRCUIT_COOLDOWN_SECONDS")
    circuit_half_open_max_probes: int = Field(default=1, alias="CIRCUIT_HALF_OPEN_MAX_PROBES")

    # Reliability: Failover Configuration
    max_failover_providers: int = Field(default=3, alias="MAX_FAILOVER_PROVIDERS")

    # Rate Limiting Configuration
    default_tenant_rpm: int = Field(default=60, alias="DEFAULT_TENANT_RPM")
    rate_limit_window_seconds: int = Field(default=60, alias="RATE_LIMIT_WINDOW_SECONDS")

    # Observability: OpenTelemetry Configuration
    otel_enabled: bool = Field(default=True, alias="OTEL_ENABLED")
    otel_service_name: str = Field(default="self-healing-llm-gateway", alias="OTEL_SERVICE_NAME")
    otel_exporter_endpoint: str | None = Field(default=None, alias="OTEL_EXPORTER_OTLP_ENDPOINT")


settings = Settings()
