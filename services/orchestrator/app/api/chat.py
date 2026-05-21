"""Chat endpoint — the analyst-facing API for asking the agent a question.

POST /api/v1/chat

Flow:
  1. Authenticate (auth middleware → request.state.session).
  2. Resolve TenantContext from the session.
  3. Resolve the tenant's Wazuh connection (DB + secrets).
  4. Resolve the model provider for the tenant.
  5. Pick a strategy from the model's capability descriptor.
  6. Run the agent loop bound to the request's tenant.
  7. Return the answer + citations + usage in a structured response.

Every model call is audited inside the loop.  Every tool call is audited
inside the dispatcher.  The audit trail is complete for any chat exchange.
"""

from typing import Annotated

import structlog
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_secrets import SecretsBackend

from app.agent import AgentLoop, get_model_for_tenant, strategy_for
from app.config import get_settings
from app.database import get_db
from app.secrets_factory import get_secrets_backend
from app.tenancy.context import TenantContext, require_tenant_context
from app.tools.base import Citation
from app.wazuh.opensearch import WazuhOpenSearchClient
from app.wazuh.resolver import get_wazuh_connection
from app.wazuh.server_api import WazuhServerApiClient

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
_settings = get_settings()


class ChatRequestBody(BaseModel):
    """User-supplied chat request.  Single-turn for Phase 2B."""

    question: str = Field(min_length=1, max_length=4000)


class ChatResponseBody(BaseModel):
    """The agent's grounded, cited answer."""

    answer: str
    citations: list[Citation]
    step_count: int
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    stop_reason: str
    loop_id: str
    strategy: str
    model_id: str


def _secrets_dep() -> SecretsBackend:
    """FastAPI dependency that yields the secrets backend.

    Wrapped so tests can override it via `app.dependency_overrides`.
    """
    return get_secrets_backend(_settings)


@router.post("", response_model=ChatResponseBody)
async def chat(
    body: ChatRequestBody,
    ctx: Annotated[TenantContext, Depends(require_tenant_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    secrets: Annotated[SecretsBackend, Depends(_secrets_dep)],
) -> ChatResponseBody:
    """Run the agent loop and return a grounded, cited answer."""
    logger.info(
        "chat_request_received",
        tenant_id=str(ctx.tenant_id),
        user_id=str(ctx.user_id),
        question_chars=len(body.question),
    )

    provider = await get_model_for_tenant(ctx, _settings, secrets)
    capability = provider.capability()
    strategy = strategy_for(capability)

    connection = await get_wazuh_connection(ctx, db, secrets)

    async with (
        WazuhOpenSearchClient(connection) as opensearch,
        WazuhServerApiClient(connection) as server_api,
    ):
        loop = AgentLoop(provider=provider, strategy=strategy)
        answer = await loop.run(
            question=body.question,
            ctx=ctx,
            db=db,
            opensearch=opensearch,
            server_api=server_api,
        )

    # Persist the audit trail produced by the loop.
    await db.commit()

    return ChatResponseBody(
        answer=answer.content,
        citations=answer.citations,
        step_count=answer.step_count,
        tool_call_count=answer.tool_call_count,
        input_tokens=answer.input_tokens,
        output_tokens=answer.output_tokens,
        stop_reason=answer.stop_reason,
        loop_id=answer.loop_id,
        strategy=strategy.name,
        model_id=capability.model_id,
    )
