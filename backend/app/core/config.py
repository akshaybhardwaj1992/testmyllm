"""Application settings, read from environment variables (see .env.example)."""
from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://testlab:testlab@localhost:5432/testlab"
    redis_url: str = "redis://localhost:6379/0"

    # Secrets: must be set in production. JWT signing key and Fernet key for credentials at rest.
    jwt_secret: str = Field(min_length=32)
    encryption_key: str = Field(min_length=40)
    jwt_ttl_minutes: int = 60 * 12
    cookie_secure: bool = False

    admin_email: str = "admin@example.com"
    admin_password: str = ""

    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    provider_timeout_s: float = 120.0
    provider_max_concurrency: int = 4
    unit_timeout_s: int = 600

    # RAG connectors (SSRF guard, decision D21). Comma-separated hosts; ".example.com" matches subdomains.
    rag_host_allowlist: str = ""
    rag_allow_private: bool = False  # true only for local development

    # Celery eager mode is for tests only.
    celery_eager: bool = False


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
