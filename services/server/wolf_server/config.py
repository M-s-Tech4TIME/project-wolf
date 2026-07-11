"""Service configuration loaded from environment variables.

All settings have defaults safe for local development.  Production deployments
must override SECRET_KEY and DATABASE_URL at minimum.
"""

from functools import lru_cache
from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The built-in SECRET_KEY placeholder. It is public in the source tree, so any
# deployment that fails to override it would let anyone forge JWTs (including
# Superuser tokens). `Settings._validate_secret_key` rejects it — and any key
# shorter than MIN_SECRET_KEY_LENGTH — at construction time, in every
# environment. See docs/07-security-and-threat-model.md (hardening backlog) for
# the plan to move the key material off-disk into a TPM/KMS root of trust (Gap 2).
DEFAULT_SECRET_KEY = "change-me-this-must-be-at-least-32-chars"  # noqa: S105
MIN_SECRET_KEY_LENGTH = 32

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
    # JWT signing key. Defaults to the public placeholder so a fresh checkout
    # imports, but `_validate_secret_key` fails closed unless it is overridden
    # with a unique, >=32-char value (the placeholder is public → forgeable
    # JWTs). Stored plaintext in .env (0600); a TPM/KMS root of trust is the
    # tracked Gap-2 hardening item.
    secret_key: str = DEFAULT_SECRET_KEY
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

    # ── Proposal approval window (ADR 0025/0029) ─────────────────────────────────
    # How long a pending action proposal stays approvable before it auto-expires
    # (state 'expired'). Was a flat 15 min, which lapsed mid-review during the
    # 6-e.4 web-test (a <vulnerability-detection> config diff expired before the
    # approver finished reading it — slow local inference + a careful human).
    # Raised to 30 min for real review headroom. Staleness is still guarded
    # INDEPENDENTLY at execute time by every class's freshness re-check (agent
    # still present / section unchanged since proposal), so a longer approval
    # window never risks applying a stale action. A future Phase 6.10 settings
    # consumer; env-only (PROPOSAL_TTL_SECONDS) for now.
    proposal_ttl_seconds: int = 1800

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
    # Ollama context window (num_ctx) for the chat AND grounding-judge models.
    # CRITICAL: Ollama's built-in default is only 4096 tokens. Wolf's system
    # prompt + full read/propose tool catalog (14 tools with JSON schemas) is
    # ~7.2K tokens BEFORE any conversation history or tool results — so the 4096
    # default silently TRUNCATES the prompt, dropping the earlier tool
    # definitions. The model then literally cannot see tools like `list_agents`
    # / `search_alerts` / `count_alerts_by_severity` and answers "no such tool"
    # in prose with zero tool calls (the 2026-07-01 regression). 16384 fits the
    # tool prompt with comfortable headroom for an 8-step guided loop's
    # accumulated tool results; still far under qwen3's 128K capability. Raise
    # it for very large environments (bigger tool results) or lower it on
    # VRAM-constrained hardware — larger num_ctx = larger KV-cache VRAM. Applied
    # to Ollama only (hosted providers carry their own large contexts and error
    # loudly rather than truncate); a future consumer of the Phase 6.10 model
    # posture GUI. One value for chat + judge so a same-tag deployment (the
    # default unified qwen3:8b) keeps ONE loaded context and never reloads
    # between a chat call and its grounding pass.
    # Full performance/hardware tuning guide (this + KV-cache quantization +
    # model posture + OLLAMA_NUM_PARALLEL, with scenario recipes + benchmarks):
    # docs/reference/model-performance-tuning.md
    ollama_num_ctx: int = 16384
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

    # ── Agent persistence (6-f.5 — operator directive: no hard step caps) ────
    # The agent loop persists until the model answers, the no-progress guard
    # trips, or the context-fit guard fires — there is NO fixed step ceiling.
    # AGENT_STEP_BREAKER is an OPTIONAL operator circuit breaker (cost
    # protection on paid per-token APIs): 0 (default) = disabled/unbounded;
    # when set >0, reaching it forces a best-effort SYNTHESIS from the evidence
    # gathered so far — never a canned failure. Phase 6.10 GUI consumer.
    agent_step_breaker: int = 0
    # Fraction of the model's effective context window the transcript may
    # occupy before the loop stops gathering and synthesizes the best answer
    # from the evidence it has. This is the natural (non-arbitrary) bound:
    # past it, hosted providers hard-400 and Ollama silently TRUNCATES the
    # transcript head (dropping the system prompt + tool catalog — the
    # 2026-07-01 num_ctx regression), so continuing cannot help.
    agent_context_fit_threshold: float = 0.8

    # ── Web research (ADR 0032 — slice 6-f.1 config seam) ───────────────────
    # Opt-in: OFF by default so a stock install never advertises web tools it
    # can't run (wolf-search is a Recommends, not a Depends). Enabling gates
    # the web_search/web_fetch/web_crawl tool REGISTRATION (6-f.3); the
    # resolver additionally fails closed if called while disabled.
    web_search_enabled: bool = False
    # Backend behind the pluggable SearchProvider adapter. `searxng` (the
    # free, self-hosted default) is the only wired backend; `brave`/`tavily`
    # are reserved per-org hosted options (ADR 0032 out-of-scope until the
    # default path is proven).
    web_search_provider: str = "searxng"  # searxng | brave | tavily
    # Where the wolf-search component listens. Loopback in every recommended
    # topology (wolf-search is wolf-server's sidecar, ADR 0032 A3.1); a
    # dedicated search tier swaps this for its mTLS-fronted private URL —
    # same seam pattern as DATABASE_URL / OLLAMA_BASE_URL. Port 1307 is the
    # operator-chosen wolf-search port (6-f.2; SearXNG's own default is 8888).
    searxng_url: str = "http://127.0.0.1:1307"
    # Tool-facing knobs (ADR 0032 A7, consumed by the 6-f.3 tools). Under the
    # SearXNG default web access is FREE and uncapped — these budgets are
    # self-protection (finite model context; a runaway crawl must not exhaust
    # wolf-server) + MSSP tenant-fairness, NOT a paywall (ADR 0032 A6 "free
    # vs bounded"). Defaults are generous; all are Phase 6.10 GUI consumers
    # (web-first configurability).
    #
    # Results returned per web_search call (the tool input may ask for fewer).
    web_search_max_results: int = 8
    # Combined web_search + web_fetch + web_crawl calls allowed per chat
    # request — the `max_uses` analog from Claude's web tools. Exhausting it
    # degrades to an honest "budget exhausted" tool error, never a hang.
    # Raised 12 → 32 in 6-f.5 (operator directive: web research "requires
    # persistence until satisfied") — still self-protection/tenant-fairness,
    # generous enough to never starve a legitimate deep investigation.
    web_search_budget_per_request: int = 32
    # Hard cap on the DECOMPRESSED response body per fetched page (a gzip
    # bomb is caught by this, not just wire size — ADR 0032 A6 §4).
    web_fetch_max_bytes: int = 2_000_000
    # Whole-fetch deadline (connect + headers + body). Also the slow-loris
    # guard: one glacial page can never stall the agent loop (A6 §14).
    web_fetch_timeout_seconds: float = 20.0
    # Bounded-crawl caps (A1/A6 §11): link-depth from the seed page and total
    # pages fetched per web_crawl call. The tool input may lower, never raise.
    web_crawl_max_depth: int = 2
    web_crawl_max_pages: int = 12
    # Crawler politeness: minimum seconds between two requests to the same
    # host during a crawl (A6 §11).
    web_crawl_per_host_rate: float = 1.0

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
    # PRIMARY vector column width (knowledge_chunks.embedding). Fully
    # configurable (ADR 0033): the SQLAlchemy model reads this at import
    # time, and the live pgvector column is reconciled by the operator
    # tool `python -m wolf_server.management.embedding_schema --apply`
    # (drops the HNSW index, re-types the column, re-embeds every chunk,
    # rebuilds the index). Changing the value WITHOUT running the tool
    # leaves the DB at the old width — inserts then fail loudly with
    # Postgres's "expected N dimensions" error, never silently.
    # pgvector constraint: HNSW indexes cap at 2000 dims; above that the
    # schema tool skips ANN indexing and search runs exact (perfect
    # recall, slower on very large corpora).
    embedding_dimension: int = 768
    # ADR 0014 — optional secondary embedding model for multi-embedding
    # retrieval. When set, the agent loop's RAG path fuses three rankers
    # via RRF (BM25 + primary vector + secondary vector). Empty default
    # = single-leg behaviour (backward compat). Typical value:
    # `nomic-embed-text-v2-moe`.
    embedding_model_aux: str = ""
    # Provider for the aux embedder. Empty = same as embedding_provider.
    embedding_provider_aux: str = ""
    # AUX vector column width (knowledge_chunks.embedding_v2). 0 (default)
    # = same as embedding_dimension. Independent so e.g. a 4096-dim
    # primary can sit next to a 768-dim aux; reconciled by the same
    # embedding_schema tool.
    embedding_dimension_aux: int = 0
    # MRL (Matryoshka) output truncation. 0 (default) = don't request — the
    # model's NATIVE dimension must equal its column width (nomic: 768).
    # Set below the native dimension for an MRL-trained model — e.g.
    # qwen3-embedding (native 4096) at 768/1024/2000 — so it fits a
    # narrower pgvector column with the officially supported
    # truncate+renormalize behaviour. Ollama applies it server-side via
    # /api/embed's `dimensions` field (probed live 2026-07-11: returns
    # 768-dim L2-normalized vectors); sentence-transformers via the
    # library's `truncate_dim`. ONLY valid for MRL-trained models — blind
    # truncation of a non-MRL model corrupts the embedding geometry.
    embedding_request_dimensions: int = 0
    embedding_request_dimensions_aux: int = 0
    # Instruction prefix applied to QUERIES ONLY (documents always embed
    # raw) for instruction-aware asymmetric retrieval models.
    # qwen3-embedding's official recipe:
    #   "Instruct: Given a web search query, retrieve relevant passages
    #    that answer the query\nQuery: "
    # Empty (default) = symmetric embedding (correct for nomic-embed-text).
    embedding_query_prefix: str = ""
    embedding_query_prefix_aux: str = ""
    # Task prefix applied to PASSAGES/DOCUMENTS at embed time (upsert,
    # seeding, re-embeds). The nomic family is trained with task prefixes
    # on BOTH sides: documents want "search_document: " and queries
    # "search_query: " (nomic-embed-text v1.5 AND v2-moe). Empty default
    # = raw passages (backward compatible). Changing a prefix changes the
    # embedding geometry — re-run `reembed --apply --force` (and/or
    # `--aux`) so stored vectors match.
    embedding_document_prefix: str = ""
    embedding_document_prefix_aux: str = ""
    # Context window for the EMBEDDING model, passed as Ollama
    # options.num_ctx per /api/embed call (0 = the model's own default).
    # qwen3-embedding supports 40960; nomic-embed-text 8192 (2048 loaded
    # default); v2-moe is hard-capped at 512. Ollama silently truncates
    # inputs beyond the loaded window, so raising this is how long chunks
    # keep full fidelity. Ignored by sentence-transformers (in-process
    # models use their native max_seq_length).
    embedding_num_ctx: int = 0
    embedding_num_ctx_aux: int = 0
    # Hard character cap applied to each input BEFORE embedding
    # (0 = uncapped). Guards models whose context cannot absorb Wolf's
    # largest chunks — v2-moe's 512-token window ≈ 1800 chars with safety
    # margin (the aux default, preserving the previous hardcoded
    # behaviour). Truncation is at the adapter so upsert, seeding and
    # re-embeds all behave identically.
    embedding_char_limit: int = 0
    embedding_char_limit_aux: int = 1800
    # Candidate oversampling for binary-quantized retrieval. Vector columns
    # wider than pgvector's 2000-dim HNSW cap are indexed via
    # binary_quantize(...)::bit(N) + Hamming distance; the store fetches
    # (candidate_limit x this factor) by Hamming, then reranks by exact
    # cosine. Higher = better recall, more rerank work. 4 is the
    # pgvector-community sweet spot; irrelevant for widths <= 2000.
    embedding_bq_oversample: int = 4

    @property
    def is_development(self) -> bool:
        return self.environment == "development"

    @property
    def is_test(self) -> bool:
        return self.environment == "test"

    @model_validator(mode="after")
    def _decode_prefix_escapes(self) -> "Settings":
        """Normalize literal ``\\n`` in embedding task prefixes to newlines.

        The qwen3-embedding instruct prefix ends "...query\\nQuery: " with a
        REAL newline. Whether the backslash-n survives as two characters
        depends on the load path: python-dotenv decodes it in double-quoted
        .env values, but `set -a; source .env` (bash) and systemd's
        `EnvironmentFile=` (how wolf-server actually runs) both keep it
        literal. Normalizing here makes every path yield the same prefix —
        no prompt prefix legitimately contains a literal backslash-n.
        """
        for attr in (
            "embedding_query_prefix",
            "embedding_query_prefix_aux",
            "embedding_document_prefix",
            "embedding_document_prefix_aux",
        ):
            value: str = getattr(self, attr)
            if "\\n" in value:
                object.__setattr__(self, attr, value.replace("\\n", "\n"))
        return self

    @model_validator(mode="after")
    def _validate_secret_key(self) -> "Settings":
        """Fail closed on an insecure JWT signing key.

        Runs on every construction (including when the default is used, which
        `field_validator` would skip) and in every environment: the placeholder
        is public in the source tree, so there is no environment where signing
        JWTs with it is acceptable.
        """
        hint = 'generate one with: python -c "import secrets; print(secrets.token_urlsafe(48))"'
        if self.secret_key == DEFAULT_SECRET_KEY:
            msg = (
                "SECRET_KEY is still the built-in default placeholder, which is public "
                "in the source tree — anyone could forge JWTs, including Superuser "
                f"tokens. Set SECRET_KEY to a unique, random value ({hint})."
            )
            raise ValueError(msg)
        if len(self.secret_key) < MIN_SECRET_KEY_LENGTH:
            msg = (
                f"SECRET_KEY must be at least {MIN_SECRET_KEY_LENGTH} characters for a "
                f"secure JWT signing key (got {len(self.secret_key)}); {hint}."
            )
            raise ValueError(msg)
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()


class EmbeddingDimensions(BaseSettings):
    """The two ints the ORM needs at import time — nothing else.

    `knowledge.models` reads the vector column widths while the module is
    being imported (SQLAlchemy DDL is static). Importing the FULL Settings
    there would run every validator — including the SECRET_KEY placeholder
    guard — in contexts that legitimately have no app secrets, e.g. CI's
    alembic-check job. This narrow model reads the SAME sources (.env +
    process env) for just the dimension knobs.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    embedding_dimension: int = 768
    embedding_dimension_aux: int = 0


@lru_cache
def get_embedding_dimensions() -> EmbeddingDimensions:
    return EmbeddingDimensions()
