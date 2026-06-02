"""Service configuration loaded from environment variables.

All settings have defaults safe for local development.  Production deployments
must override SECRET_KEY and DATABASE_URL at minimum.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor for path-shaped defaults that need to be CWD-independent.
# `app/config.py` lives at `services/orchestrator/app/config.py`, so
# parents[3] is the repo root. The wolf-cert CLI writes certs into
# `<repo>/.local/certs/` by default, and the orchestrator launcher
# needs to find them there regardless of which directory `python -m
# app` was invoked from (in practice `services/orchestrator/`, per
# docs/restart.md). The fallback to a relative path matters for a
# future packaged install where the source tree shape changes.
try:
    _PROJECT_ROOT = Path(__file__).resolve().parents[3]
except IndexError:  # pragma: no cover — defensive
    _PROJECT_ROOT = Path.cwd()


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
    # Regex evaluated IN ADDITION to the exact list above. Default matches
    # any private-network IP (192.168/16, 10/8, 172.16/12) or loopback on
    # any port, so dev LAN-IP rotations don't require an env edit. Set to
    # "" in production to disable.
    cors_allow_origin_regex: str = (
        r"^https?://("
        r"localhost|127\.0\.0\.1|\[::1\]"
        r"|192\.168\.\d+\.\d+"
        r"|10\.\d+\.\d+\.\d+"
        r"|172\.(?:1[6-9]|2\d|3[01])\.\d+\.\d+"
        r")(?::\d+)?$"
    )

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_allow_origins.split(",") if o.strip()]

    # ── Network bind + TLS (Phase 5.4-c) ───────────────────────────────────
    # The orchestrator binds to 0.0.0.0 so it's reachable from any
    # interface on the host (loopback, LAN, container network). This
    # matches the dev pattern in docs/restart.md and the prod pattern
    # for a Wolf instance accessed by browsers + relay daemons on the
    # same LAN. Override via BIND_HOST=127.0.0.1 to restrict.
    bind_host: str = "0.0.0.0"  # noqa: S104  intentional all-interfaces bind
    bind_port: int = 8000

    # Paths to the TLS cert + key issued by `wolf-cert init`. Defaults
    # are anchored at the project root (see `_PROJECT_ROOT` above), so
    # a fresh `wolf-cert init` followed by `python -m app` automatically
    # flips the orchestrator to HTTPS — no env-edit dance, regardless
    # of which directory `python -m app` was invoked from.
    #
    # The cert FILES themselves are the signal — when both exist the
    # launcher in `app.__main__` passes uvicorn `--ssl-keyfile` and
    # `--ssl-certfile`; when either is missing it falls back to plain
    # HTTP (today's dev behaviour). Operators who keep their certs
    # elsewhere override these via TLS_CERT_PATH / TLS_KEY_PATH in .env.
    tls_cert_path: str = str(_PROJECT_ROOT / ".local/certs/orchestrator/cert.pem")
    tls_key_path: str = str(_PROJECT_ROOT / ".local/certs/orchestrator/key.pem")

    # ── Model defaults (per-tenant overrides come in a later phase) ────────
    default_model_provider: str = "ollama"  # anthropic | openai | ollama
    # Default model: qwen3:4b (Apache 2.0).  Switched from llama3.2 on
    # 2026-05-22 per docs/decisions/0004-model-switch-llama3.2-to-qwen3-4b.md
    # — license posture (Llama Community License is not OSI-open) plus a
    # higher overall probe score on the dev hardware (0.75 vs 0.68).  Same
    # strategy tier (`guided`) so agent-loop behaviour is unchanged.
    default_model_id: str = "qwen3:4b"
    # Name of the secret in the secrets backend holding the API key (only
    # needed for anthropic/openai).  Leave empty for ollama.
    default_model_api_key_ref: str = ""
    ollama_base_url: str = "http://localhost:11434"
    openai_base_url: str = "https://api.openai.com/v1"

    # ── Grounding validator (Phase 3 Slice 2B follow-up) ──────────────────
    # The validator can use a model DIFFERENT from the chat model. Default
    # is `default_model_id` so the validator runs the same model as chat,
    # which is correct for small dev deployments (one model loaded, low
    # latency). In production an operator may want the judge to use a
    # stronger model (e.g. `qwen3.6:27b` via Ollama, or a hosted frontier
    # via OpenRouter). Leave empty to inherit; set the model id to override.
    grounding_judge_model_id: str = ""
    # Provider for the judge model. Falls back to default_model_provider
    # when grounding_judge_model_id is empty. Useful for "chat on Ollama,
    # judge on OpenAI-compatible hosted API."
    grounding_judge_model_provider: str = ""
    # When the judge provider is openai (i.e. OpenRouter / hosted), this
    # names the secret key holding the API token. Leave empty for ollama.
    grounding_judge_api_key_ref: str = ""

    # ── Embedding stack (Phase 3 — knowledge layer) ────────────────────────
    # `ollama` (default) reuses the Ollama daemon already running for the LLM
    # — no torch in the orchestrator's wheel set; recommended per ADR 0007.
    # `sentence-transformers` runs in-process and requires the optional
    # `embeddings-local` extra (`uv sync --extra embeddings-local`).
    embedding_provider: str = "ollama"  # ollama | sentence-transformers
    # Model identifier for the active provider.
    #   - ollama:                Ollama tag, e.g. "nomic-embed-text"
    #   - sentence-transformers: HuggingFace name, e.g. "BAAI/bge-base-en-v1.5"
    # Both default to a 768-dim model so the knowledge_chunks.embedding
    # column width is honored without a migration.
    embedding_model: str = "nomic-embed-text"
    # Hard contract — must match knowledge_chunks.embedding column width.
    embedding_dimension: int = 768
    # ADR 0014 — optional secondary embedding model for multi-embedding
    # retrieval. When set, the agent loop's RAG path fuses three rankers
    # via RRF (BM25 + primary vector + secondary vector). Empty default
    # = single-leg behaviour (backward compat). Typical value:
    # `nomic-embed-text-v2-moe`. Must produce vectors of the same
    # dimension as embedding_dimension (the secondary column shares the
    # primary column's pgvector width).
    embedding_model_aux: str = ""
    # Provider for the aux embedder. Empty = same as embedding_provider.
    embedding_provider_aux: str = ""

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"


@lru_cache
def get_settings() -> Settings:
    return Settings()
