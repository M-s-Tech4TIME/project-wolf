"""wolf-server — FastAPI application entrypoint.

Startup order:
  1. Configure logging and tracing.
  2. Run database migrations (idempotent — safe to run on every start).
  3. Mount middleware (auth, CORS).
  4. Register routers.
"""

from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from wolf_common.errors import WolfError
from wolf_common.logging import configure_logging
from wolf_common.tracing import configure_tracing

from wolf_server.auth.middleware import AuthMiddleware
from wolf_server.auth.mtls_middleware import MtlsMiddleware
from wolf_server.config import get_settings

logger = structlog.get_logger(__name__)
_settings = get_settings()


# ── Startup / shutdown ───────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:  # noqa: ANN401
    configure_logging(_settings.log_level, _settings.environment)
    configure_tracing(
        service_name="wolf-server",
        otlp_endpoint=_settings.otel_exporter_otlp_endpoint,
        environment=_settings.environment,
    )
    logger.info(
        "wolf_server_starting",
        environment=_settings.environment,
        log_level=_settings.log_level,
    )

    # Phase 5.8-a: tolerate wolf-database still coming up. Per ADR
    # 0016 v3 every Wolf systemd unit is fully independent (no
    # After=/Requires=/Wants= between them), so a fresh boot may
    # start wolf-server before wolf-database is accepting
    # connections. Block until it is, with backoff, then run
    # migrations.
    await _wait_for_database()
    await _run_migrations()

    # Register all read tools + Phase 6 propose tools with the runtime + schema registries.
    from wolf_server.tools.registration import (  # noqa: PLC0415
        register_all_propose_tools,
        register_all_read_tools,
    )

    register_all_read_tools()
    register_all_propose_tools()

    # Model-config self-check (stability): validate the configured chat +
    # grounding-judge providers now so a malformed model config (unknown
    # provider, or an API-key ref that resolves to no secret — classically a
    # stray inline '#' comment on a systemd EnvironmentFile value line) fails
    # LOUDLY at boot instead of silently 500-ing every chat request.
    from wolf_server.agent.model_resolver import check_model_config  # noqa: PLC0415
    from wolf_server.secrets_factory import get_secrets_backend  # noqa: PLC0415

    # Fail-SAFE: a startup diagnostic must NEVER crash the server. If even the
    # secrets backend can't be built (e.g. an invalid Fernet key in a smoke/test
    # env), skip the check with a warning rather than taking wolf-server down.
    try:
        _secrets_backend = get_secrets_backend(_settings)
        _model_problems = await check_model_config(_settings, _secrets_backend)
        for _problem in _model_problems:
            logger.error("model_config_invalid", problem=_problem)
        if _model_problems:
            logger.error(
                "model_config_invalid_summary",
                count=len(_model_problems),
                hint="check DEFAULT_MODEL_* / GROUNDING_JUDGE_* env (no inline '#' in values)",
            )
        else:
            logger.info(
                "model_config_ok",
                chat_provider=_settings.default_model_provider,
                chat_model=_settings.default_model_id,
                judge_model=_settings.grounding_judge_model_id or "(chat model)",
            )
    except Exception as _exc:  # noqa: BLE001 — diagnostic only; never fatal at boot
        logger.warning("model_config_check_skipped", error=str(_exc))

    # Timed auto-reversal scheduler (slice 6-d.3): the background sweep that
    # reverses a timed block when its window expires. A cheap no-op when no timed
    # blocks are due; disable via AUTO_REVERSAL_ENABLED=0.
    import asyncio  # noqa: PLC0415  defer the import; only needed here at startup
    from contextlib import suppress  # noqa: PLC0415

    from wolf_server.gateway.scheduler import run_auto_reversal_scheduler  # noqa: PLC0415

    scheduler_task: asyncio.Task[None] | None = None
    scheduler_stop = asyncio.Event()
    if _settings.auto_reversal_enabled:
        scheduler_task = asyncio.create_task(
            run_auto_reversal_scheduler(
                interval_seconds=_settings.auto_reversal_sweep_interval_seconds,
                stop_event=scheduler_stop,
            )
        )

    logger.info("wolf_server_ready")
    yield
    logger.info("wolf_server_stopping")
    scheduler_stop.set()
    if scheduler_task is not None:
        scheduler_task.cancel()
        with suppress(asyncio.CancelledError):
            await scheduler_task


# Backoff schedule used by `_wait_for_database`. Sums to ~120s
# across the first 12 attempts; we cycle the last value
# indefinitely until the overall timeout is hit. Exposed here as a
# module constant so tests can substitute a faster version.
_DB_BACKOFF_SECONDS: tuple[float, ...] = (0.5, 1.0, 2.0, 5.0, 10.0, 20.0, 30.0)
_DB_WAIT_TIMEOUT_SECONDS: float = 120.0


async def _wait_for_database(
    *,
    backoff: tuple[float, ...] = _DB_BACKOFF_SECONDS,
    timeout: float = _DB_WAIT_TIMEOUT_SECONDS,
) -> None:
    """Poll DATABASE_URL with a simple `SELECT 1` until it responds.

    Wolf-server can't usefully do anything before its DB is up — at
    minimum, alembic needs to run on startup. Rather than crashing
    on the first ConnectionRefused, we retry on a backoff schedule
    so a freshly-rebooted host where wolf-database is still coming
    up doesn't degenerate into a `Restart=on-failure` flap loop.

    Logs `database_unreachable_retrying` at warning level on each
    miss so the operator can grep for it. On success, returns.
    On total timeout, raises — at that point wolf-server can't
    start no matter how patient we are.
    """
    import asyncio  # noqa: PLC0415  defer the import; only needed at startup
    import itertools  # noqa: PLC0415

    from sqlalchemy import text  # noqa: PLC0415
    from sqlalchemy.ext.asyncio import create_async_engine  # noqa: PLC0415

    elapsed = 0.0
    backoff_iter = itertools.cycle(backoff)
    while True:
        engine = create_async_engine(_settings.database_url)
        try:
            async with engine.connect() as conn:
                await conn.execute(text("SELECT 1"))
            await engine.dispose()
            logger.info("database_reachable", elapsed_s=round(elapsed, 1))
            return
        except Exception as exc:
            await engine.dispose()
            if elapsed >= timeout:
                logger.error(
                    "database_unreachable_giving_up",
                    elapsed_s=round(elapsed, 1),
                    timeout_s=timeout,
                    error=str(exc),
                )
                raise
            delay = next(backoff_iter)
            logger.warning(
                "database_unreachable_retrying",
                elapsed_s=round(elapsed, 1),
                next_attempt_in_s=delay,
                error=str(exc),
            )
            await asyncio.sleep(delay)
            elapsed += delay


def _find_alembic_ini() -> Path:
    """Locate alembic.ini in dev or .deb-installed layouts.

    Dev workspace: services/server/alembic.ini (one dir above
    wolf_server/main.py).
    Prod .deb install: /usr/lib/wolf-server/alembic.ini (alongside
    the .venv where wolf_server is installed).
    """
    deb_path = Path("/usr/lib/wolf-server/alembic.ini")
    if deb_path.exists():
        return deb_path
    dev_path = Path(__file__).resolve().parent.parent / "alembic.ini"
    if dev_path.exists():
        return dev_path
    raise FileNotFoundError(f"alembic.ini not found at {deb_path} or {dev_path}")


async def _run_migrations() -> None:
    """Run Alembic migrations programmatically on startup."""
    import asyncio  # noqa: PLC0415
    from concurrent.futures import ThreadPoolExecutor  # noqa: PLC0415

    from alembic import command  # noqa: PLC0415
    from alembic.config import Config  # noqa: PLC0415

    cfg = Config(str(_find_alembic_ini()))
    # In-process run: alembic.ini's logging config must NOT reconfigure
    # the app's live logging (env.py honors this attribute). Without it,
    # fileConfig sets root level WARN + re-homes handlers, silencing
    # uvicorn + structlog for the rest of the process lifetime.
    cfg.attributes["configure_logger"] = False

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        await loop.run_in_executor(pool, command.upgrade, cfg, "head")
    logger.info("migrations_applied")


# ── App factory ──────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(
        title="wolf-server",
        description=(
            "Wolf Server — agentic AI platform for Wazuh, the brain component (per ADR 0016)."
        ),
        version="0.1.0",
        lifespan=lifespan,
        # Docs are served at /docs and /redoc; disable in production if desired.
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── Middleware ──────────────────────────────────────────────────────────
    # Dev: any origin (regex) so a LAN-IP rotation isn't a paper-cut.
    # Production: set CORS_ALLOW_ORIGIN_REGEX="" and CORS_ALLOW_ORIGINS to
    # the exact wolf-dashboard URL(s) — see wolf_server.config.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origin_list,
        allow_origin_regex=_settings.cors_allow_origin_regex or None,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuthMiddleware)
    # Starlette runs the LAST-added middleware OUTERMOST, so adding
    # mTLS last puts it ahead of AuthMiddleware in the request path —
    # an unauthorized caller is rejected before any auth code runs.
    # Only mounted when the operator has run `wolf-cert init` (the CA
    # + server leaf exist); otherwise we leave it off so the dev
    # no-certs path is unaffected.
    if _settings.mtls_enabled:
        app.add_middleware(
            MtlsMiddleware,
            allowed_cns=_settings.mtls_allowed_client_cn_list,
        )
        logger.info(
            "mtls_middleware_mounted",
            allowed_cns=_settings.mtls_allowed_client_cn_list,
        )

    # ── Routers ─────────────────────────────────────────────────────────────
    from wolf_server.api.action_proposals import (  # noqa: PLC0415
        router as action_proposals_router,
    )
    from wolf_server.api.auth import router as auth_router  # noqa: PLC0415
    from wolf_server.api.chat import router as chat_router  # noqa: PLC0415
    from wolf_server.api.org_management import router as org_management_router  # noqa: PLC0415
    from wolf_server.api.organizations import router as organizations_router  # noqa: PLC0415
    from wolf_server.api.superuser import router as superuser_router  # noqa: PLC0415
    from wolf_server.api.wazuh_credentials import (
        router as wazuh_credentials_router,  # noqa: PLC0415
    )
    from wolf_server.api.wazuh_topology import router as wazuh_topology_router  # noqa: PLC0415

    app.include_router(auth_router)
    app.include_router(chat_router)
    app.include_router(superuser_router)
    app.include_router(organizations_router)
    app.include_router(org_management_router)
    app.include_router(wazuh_topology_router)
    app.include_router(wazuh_credentials_router)
    app.include_router(action_proposals_router)

    # ── Error handlers ──────────────────────────────────────────────────────

    @app.exception_handler(WolfError)
    async def wolf_error_handler(request: Request, exc: WolfError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.http_status,
            content={"error": exc.error_code, "detail": str(exc)},
        )

    # ── Health check ─────────────────────────────────────────────────────────

    @app.get("/healthz", tags=["ops"], include_in_schema=False)
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "wolf-server"}

    return app


app = create_app()
