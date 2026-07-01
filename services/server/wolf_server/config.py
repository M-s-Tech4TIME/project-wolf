"""Service configuration loaded from environment variables.

All settings have defaults safe for local development.  Production deployments
must override SECRET_KEY and DATABASE_URL at minimum.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Anchor for path-shaped defaults that need to be CWD-independent.
# `wolf_server/config.py` lives at
# `services/server/wolf_server/config.py`, so parents[3] is the
# repo root. The wolf-cert CLI writes certs into `<repo>/.local/certs/`
# by default, and the wolf-server launcher needs to find them there
# regardless of which directory `python -m wolf_server` was invoked
# from (in practice `services/server/`, per docs/restart.md). The
# fallback to a relative path matters for a future packaged install
# where the source tree shape changes.
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
    # Session-blacklist backend (Phase 6.5-g). Empty = in-memory store
    # (correct for the default single-process deployment). Set to e.g.
    # redis://localhost:6379/0 to use an operator-managed Redis server
    # (required for multi-worker installs; survives wolf-server restarts).
    redis_url: str = ""

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
    # credentials. Post-Phase-5.6-a browsers always go through
    # wolf-dashboard's reverse proxy, so wolf-server typically never
    # sees a browser Origin header in normal operation. CORS is kept
    # configured anyway for ops use (curl with -H "Origin:..." from a
    # workstation, direct API testing) and as a defence-in-depth layer
    # if someone bypasses the proxy. Empty in production unless you
    # explicitly configure it.
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
    # wolf-server binds to 0.0.0.0 by default so it's reachable from
    # any interface on the host (loopback, LAN, container network).
    # In a distributed deployment (per ADR 0016) operators usually
    # leave this as 0.0.0.0 since mTLS enforces who can actually
    # connect. In all-in-one deployments they can tighten to
    # BIND_HOST=127.0.0.1 since only the local wolf-dashboard
    # process needs to reach it.
    bind_host: str = "0.0.0.0"  # noqa: S104  intentional all-interfaces bind
    bind_port: int = 7860

    # Paths to the TLS cert + key issued by `wolf-cert init`. Defaults
    # are anchored at the project root (see `_PROJECT_ROOT` above), so
    # a fresh `wolf-cert init` followed by `python -m wolf_server`
    # automatically flips wolf-server to HTTPS — no env-edit dance,
    # regardless of which directory the launcher was invoked from.
    #
    # The cert FILES themselves are the signal — when both exist the
    # launcher in `wolf_server.__main__` passes uvicorn `--ssl-keyfile`
    # and `--ssl-certfile`; when either is missing it falls back to
    # plain HTTP (today's dev behaviour). Operators who keep their
    # certs elsewhere override these via TLS_CERT_PATH / TLS_KEY_PATH
    # in .env.
    #
    # Phase 5.5 leaf-name rename: `orchestrator/` → `server/`. The
    # wolf-cert CLI emits the new path; this default matches.
    tls_cert_path: str = str(_PROJECT_ROOT / ".local/certs/server/cert.pem")
    tls_key_path: str = str(_PROJECT_ROOT / ".local/certs/server/key.pem")

    # ── mTLS (Phase 5.6-c) ─────────────────────────────────────────────────
    # Path to the Wolf CA cert. When this file exists (alongside the server
    # cert + key above), the launcher passes uvicorn `ssl_ca_certs=<this>`
    # + `ssl_cert_reqs=1` (ssl.CERT_OPTIONAL), enabling client-cert
    # presentation at the TLS layer. The ASGI MtlsMiddleware then enforces
    # the CN allowlist + audit-logs decisions. When the file is missing
    # (dev no-certs path), mTLS is off and any client can connect.
    mtls_ca_path: str = str(_PROJECT_ROOT / ".local/certs/ca/ca-cert.pem")
    # Comma-separated list of accepted client-cert Subject CNs. Default is
    # the dashboard's reverse-proxy identity (Phase 5.6-b). Future relay
    # daemons get their own CN added here, e.g.
    # "wolf-dashboard-client,wolf-relay-acme,wolf-relay-beta".
    mtls_allowed_client_cns: str = "wolf-dashboard-client"

    @property
    def mtls_enabled(self) -> bool:
        """mTLS is on iff the CA + server cert/key all exist on disk.

        This mirrors the cert-files-are-the-signal contract that
        Phase 5.4-c established for HTTPS itself: the operator runs
        `wolf-cert init` and the next launcher start picks up mTLS
        automatically, no env flag.
        """
        from pathlib import Path

        return (
            Path(self.mtls_ca_path).is_file()
            and Path(self.tls_cert_path).is_file()
            and Path(self.tls_key_path).is_file()
        )

    @property
    def mtls_allowed_client_cn_list(self) -> list[str]:
        return [cn.strip() for cn in self.mtls_allowed_client_cns.split(",") if cn.strip()]

    # ── Same-network verification gate (Phase 6.5-h.2, ADR 0018 item 9) ──────
    # When enabled, the invite-verification endpoint (api/auth.py
    # verify-invite) only flips an account to `verified` if the request's
    # real client IP falls inside one of wolf-server's own NIC CIDRs (the
    # IP is propagated by the dashboard edge proxy over mTLS).
    #
    # OFF by default. This gate is intrinsically an ON-PREM, single-network
    # control: it checks membership in *wolf-server's* network. In an MSSP
    # deployment wolf-server lives in the provider's datacenter while client
    # orgs are remote — so a default-ON gate would permanently block every
    # remote client from verifying. MSSP is a first-class Wolf target, so the
    # safe default is OFF; on-prem single-network operators opt in with
    # `SAME_NETWORK_GATE_ENABLED=1`. The MSSP-correct evolution is per-org
    # trusted networks (each org's own CIDRs) — a later phase.
    #
    # Today this is env-only; the future Superuser config-settings system
    # (DB source of truth ⇄ Web Settings GUI ⇄ Wolf CLI ⇄ env, Superuser-only,
    # audited — ADR 0019 web-first-configurability) makes it a synced toggle.
    # The startup banner prints the live state + this var name.
    same_network_gate_enabled: bool = False

    # ── Timed auto-reversal scheduler (slice 6-d.3, ADR 0028) ────────────────────
    # The in-process sweep that automatically reverses a TIMED block when its
    # window expires (`auto_unblock_at`). Default ON — it's a cheap no-op when no
    # timed blocks are due; timed blocks are opt-in per proposal. Disable to pause
    # the sweep entirely. Interval is how often the sweep runs (seconds). A future
    # Phase 6.10 settings consumer; env-only for now.
    auto_reversal_enabled: bool = True
    auto_reversal_sweep_interval_seconds: int = 60

    # ── Model defaults (per-organization overrides come in a later phase) ────────
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
    # OpenRouter (OpenAI-compatible hosted frontier models) — a SELECTABLE
    # provider (ADR 0030), not the default. To use: set default_model_provider
    # (and/or grounding_judge_model_provider) = "openrouter", default_model_id =
    # an OpenRouter slug (e.g. "nvidia/nemotron-3-ultra-550b-a55b:free" or
    # "openrouter/owl-alpha"), store the API key via the set_secret CLI, and
    # point *_API_KEY_REF at it. Free models are $0 but share a free-tier DAILY
    # REQUEST CAP; local Ollama stays the default (free, uncapped, on-prem).
    # The adapter posts to {base}/v1/chat/completions, so base omits /v1.
    openrouter_base_url: str = "https://openrouter.ai/api"
    openrouter_referer: str = "https://github.com/wolf-soc/wolf"  # OpenRouter attribution
    openrouter_title: str = "Wolf"

    # ── Provider failover chain (2026-07-01) ──────────────────────────────
    # When FALLBACK_MODEL_ID is set, Wolf wraps the chat AND grounding-judge
    # provider in a FailoverProvider: the configured primary is tried first,
    # and on ANY failure (rate-limit / quota, timeout, 5xx, malformed request,
    # provider outage) the request transparently continues on this fallback —
    # the analyst never sees a broken stream. Intended posture: an org
    # configures a hosted primary (e.g. OpenRouter) and names local Ollama
    # here as the safety net, so a capped/erroring cloud model never leaves
    # the analyst without an answer.
    #
    # Leave FALLBACK_MODEL_ID empty (the default) for NO chain — Wolf's default
    # primary is already local Ollama, so there is nothing to fail over to.
    # Per-organization model configuration is a later phase; this global seam
    # is the single-org path and keeps single-org ↔ MSSP parity. Provider
    # defaults to "ollama" when a fallback id is set without a provider.
    fallback_model_provider: str = ""  # anthropic | openai | openrouter | ollama
    fallback_model_id: str = ""
    fallback_model_api_key_ref: str = ""

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

    # ── Grounding execution mode (ADR 0026) ────────────────────────────────
    # WHEN grounding runs relative to the answer stream. Orthogonal to the
    # judge MODEL (ADR 0024 posture). Today env-only; Phase 6.10 promotes both
    # to a Superuser GUI control (ADR 0019), the third concrete 6.10 consumer.
    #   - blocking     (default): the judge is awaited BEFORE the `answer`
    #                  event; the answer surfaces already annotated + counted.
    #                  Today's verified behavior — zero regression.
    #   - deferred     (recommended): the `answer` event fires immediately with
    #                  raw content + grounding_pending; the judge then runs and
    #                  a follow-up `grounding.completed` patches in the
    #                  annotated content + counts. Time-to-readable-answer drops
    #                  to the token stream alone; chips arrive a moment later.
    #   - incremental: like deferred, but claims are judged in CONCURRENT
    #                  batches and chips pop in progressively (one
    #                  `grounding.partial` per batch). Real wall-clock win on
    #                  OLLAMA_NUM_PARALLEL>=2 / ample VRAM; on a constrained
    #                  single GPU the batches serialize and it degrades
    #                  gracefully to ~deferred.
    # Unknown values fall back to "blocking" (see grounding_mode_normalized).
    # The live default is "deferred" (set in .env) per the 2026-06-21 web-test —
    # the operator preferred its UX; this code default stays "blocking" as the
    # conservative no-.env fallback. (An evidence-scope "cited" trim was tried
    # and PULLED — it starved the judge; see ADR 0026 addendum. Smart evidence
    # selection is deferred to the grounding-enrichment phase.)
    grounding_mode: str = "blocking"

    @property
    def grounding_mode_normalized(self) -> str:
        """Lower-cased, validated grounding mode. Unknown → 'blocking'."""
        mode = self.grounding_mode.strip().lower()
        return mode if mode in {"blocking", "deferred", "incremental"} else "blocking"

    # ── Embedding stack (Phase 3 — knowledge layer) ────────────────────────
    # `ollama` (default) reuses the Ollama daemon already running for the LLM
    # — no torch in wolf-server's wheel set; recommended per ADR 0007.
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
