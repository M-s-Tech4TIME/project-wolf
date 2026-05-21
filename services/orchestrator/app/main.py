"""Wolf Orchestrator — FastAPI application entrypoint.

Startup order:
  1. Configure logging and tracing.
  2. Run database migrations (idempotent — safe to run on every start).
  3. Mount middleware (auth, CORS).
  4. Register routers.
"""

from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from wolf_common.errors import WolfError
from wolf_common.logging import configure_logging
from wolf_common.tracing import configure_tracing

from app.auth.middleware import AuthMiddleware
from app.config import get_settings

logger = structlog.get_logger(__name__)
_settings = get_settings()


# ── Startup / shutdown ───────────────────────────────────────────────────────


@asynccontextmanager
async def lifespan(app: FastAPI) -> Any:  # noqa: ANN401
    configure_logging(_settings.log_level, _settings.environment)
    configure_tracing(
        service_name="wolf-orchestrator",
        otlp_endpoint=_settings.otel_exporter_otlp_endpoint,
        environment=_settings.environment,
    )
    logger.info(
        "orchestrator_starting",
        environment=_settings.environment,
        log_level=_settings.log_level,
    )

    # Run migrations on startup so `make up` → ready with no manual step.
    await _run_migrations()

    # Register all Phase 2A read tools with the runtime + schema registries.
    from app.tools.registration import register_all_read_tools  # noqa: PLC0415

    register_all_read_tools()

    logger.info("orchestrator_ready")
    yield
    logger.info("orchestrator_stopping")


async def _run_migrations() -> None:
    """Run Alembic migrations programmatically on startup."""
    import asyncio
    from concurrent.futures import ThreadPoolExecutor

    from alembic import command
    from alembic.config import Config

    cfg = Config("alembic.ini")

    loop = asyncio.get_event_loop()
    with ThreadPoolExecutor(max_workers=1) as pool:
        await loop.run_in_executor(pool, command.upgrade, cfg, "head")
    logger.info("migrations_applied")


# ── App factory ──────────────────────────────────────────────────────────────


def create_app() -> FastAPI:
    app = FastAPI(
        title="Wolf Orchestrator",
        description="Agentic AI platform for Wazuh — orchestrator service",
        version="0.1.0",
        lifespan=lifespan,
        # Docs are served at /docs and /redoc; disable in production if desired.
        docs_url="/docs",
        redoc_url="/redoc",
    )

    # ── Middleware ──────────────────────────────────────────────────────────
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:3000"] if _settings.is_development else [],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_middleware(AuthMiddleware)

    # ── Routers ─────────────────────────────────────────────────────────────
    from app.api.auth import router as auth_router  # noqa: PLC0415

    app.include_router(auth_router)

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
        return {"status": "ok", "service": "wolf-orchestrator"}

    return app


app = create_app()
