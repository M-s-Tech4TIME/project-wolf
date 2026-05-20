"""Wolf Gateway — Phase 0 stub.

The gateway is a separate service with separate credentials.  Its full
implementation (proposal state machine, execute tools, approval tokens)
is built in Phase 6.

Phase 0: health check only, so docker-compose.yml can start it successfully.
"""

from fastapi import FastAPI

app = FastAPI(
    title="Wolf Gateway",
    description="Approval & Action Gateway — Phase 0 stub",
    version="0.1.0",
)


@app.get("/healthz", include_in_schema=False)
async def healthz() -> dict[str, str]:
    return {"status": "ok", "service": "wolf-gateway"}
