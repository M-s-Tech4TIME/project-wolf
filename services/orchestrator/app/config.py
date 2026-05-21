"""Service configuration loaded from environment variables.

All settings have defaults safe for local development.  Production deployments
must override SECRET_KEY and DATABASE_URL at minimum.
"""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # ── Application ────────────────────────────────────────────────────────
    environment: str = "development"
    log_level: str = "INFO"

    # ── Database ───────────────────────────────────────────────────────────
    database_url: str = "postgresql+asyncpg://wolf:wolf_dev_password@localhost:5432/wolf"

    # ── Auth / session ─────────────────────────────────────────────────────
    secret_key: str = "change-me-this-must-be-at-least-32-chars"  # noqa: S105
    # JWT algorithm and token lifetime
    jwt_algorithm: str = "HS256"
    access_token_expire_minutes: int = 60
    refresh_token_expire_days: int = 7

    # OIDC (optional — operator-configured)
    oidc_issuer: str = ""
    oidc_client_id: str = ""
    oidc_client_secret: str = ""

    # ── Secrets backend ────────────────────────────────────────────────────
    secrets_backend: str = "file"
    secrets_file_path: str = "/run/secrets/wolf_secrets.enc"
    # Fernet key — generate: uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"  # noqa: E501
    secrets_file_key: str = ""

    # ── Observability ──────────────────────────────────────────────────────
    otel_exporter_otlp_endpoint: str = ""  # empty = no export

    # ── Gateway ────────────────────────────────────────────────────────────
    gateway_url: str = "http://wolf-gateway:8001"

    # ── CORS ──────────────────────────────────────────────────────────────
    # Comma-separated list of allowed origins for browser requests with
    # credentials.  Add the URL the analyst's browser uses to reach the
    # frontend.  Empty in production unless you explicitly configure it.
    cors_allow_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    # ── Model defaults (per-tenant overrides come in a later phase) ────────
    default_model_provider: str = "ollama"  # anthropic | openai | ollama
    default_model_id: str = "llama3.2"
    # Name of the secret in the secrets backend holding the API key (only
    # needed for anthropic/openai).  Leave empty for ollama.
    default_model_api_key_ref: str = ""
    ollama_base_url: str = "http://localhost:11434"
    openai_base_url: str = "https://api.openai.com/v1"

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
