from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration loaded from environment variables."""

    app_name: str = "eSoft AgentOS API"
    app_version: str = "0.1.0"
    environment: str = "development"
    log_level: str = "INFO"
    database_url: str = "postgresql://agentos:agentos_dev_only@localhost:5432/agentos"
    redis_url: str = "redis://localhost:6379/0"
    cors_origins: str = "http://localhost:3000"

    auth_enabled: bool = False
    keycloak_issuer: str | None = None
    keycloak_audience: str = "agentos-api"
    keycloak_jwks_url: str | None = None
    keycloak_jwks_cache_seconds: int = 300
    tenant_claim: str = "tenant_id"
    openai_api_key: str | None = None
    connector_encryption_key: str | None = None
    connector_encryption_key_version: str = "v1"
    connector_allowed_hosts: str = ""
    tool_execution_enabled: bool = False

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    @property
    def allowed_origins(self) -> list[str]:
        return [origin.strip() for origin in self.cors_origins.split(",") if origin.strip()]

    @property
    def allowed_connector_hosts(self) -> set[str]:
        return {
            host.strip().lower().rstrip(".")
            for host in self.connector_allowed_hosts.split(",")
            if host.strip()
        }


@lru_cache
def get_settings() -> Settings:
    return Settings()
