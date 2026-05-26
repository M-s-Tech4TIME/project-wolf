"""Chat endpoints — analyst-facing APIs for asking the agent a question.

POST /api/v1/chat          — request-response; returns the final answer
POST /api/v1/chat/stream   — Server-Sent Events; yields loop events as
                              they happen, ending with the final answer

Both endpoints share the same setup (auth → tenant context → wazuh +
model + strategy → AgentLoop).  The streaming variant additionally hands
the loop an `event_callback` that pushes each transition onto an
asyncio.Queue consumed by the SSE response generator.

Every model call is audited inside the loop.  Every tool call is audited
inside the dispatcher.  The audit trail is complete for any chat exchange.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Annotated, Literal

import structlog
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_secrets import SecretsBackend

from app.agent import AgentLoop, get_model_for_tenant, strategy_for
from app.agent.events import LoopEvent
from app.config import get_settings
from app.database import get_db
from app.knowledge.embeddings import OllamaEmbeddingAdapter
from app.knowledge.store import PgvectorKnowledgeStore
from app.secrets_factory import get_secrets_backend
from app.tenancy.context import TenantContext, require_tenant_context
from app.tools.base import Citation
from app.wazuh.opensearch import WazuhOpenSearchClient
from app.wazuh.resolver import get_wazuh_connection
from app.wazuh.server_api import WazuhServerApiClient

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/api/v1/chat", tags=["chat"])
_settings = get_settings()


class ConversationTurn(BaseModel):
    """One past turn the client wants the agent to remember.

    Only ``user`` and ``assistant`` turns are supported; tool results from
    prior turns are not re-played because they may be stale.  This keeps
    the wire surface small and the contract honest.
    """

    role: Literal["user", "assistant"]
    content: str = Field(max_length=20_000)


class ChatRequestBody(BaseModel):
    """User-supplied chat request.

    For a new conversation, leave ``history`` empty.  For follow-up turns,
    pass the prior user/assistant pairs in order so the agent has context.
    """

    question: str = Field(min_length=1, max_length=4000)
    history: list[ConversationTurn] = Field(default_factory=list, max_length=40)


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
    knowledge_store = PgvectorKnowledgeStore(
        db, OllamaEmbeddingAdapter(_settings.ollama_base_url)
    )

    async with (
        WazuhOpenSearchClient(connection) as opensearch,
        WazuhServerApiClient(connection) as server_api,
    ):
        loop = AgentLoop(provider=provider, strategy=strategy)
        answer = await loop.run(
            question=body.question,
            history=[(t.role, t.content) for t in body.history],
            ctx=ctx,
            db=db,
            opensearch=opensearch,
            server_api=server_api,
            knowledge_store=knowledge_store,
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


def _sse_format(event: LoopEvent) -> str:
    """Serialize one LoopEvent in SSE wire format."""
    payload = json.dumps(event.data, default=str)
    return f"event: {event.type}\ndata: {payload}\n\n"


@router.post("/stream")
async def chat_stream(
    body: ChatRequestBody,
    ctx: Annotated[TenantContext, Depends(require_tenant_context)],
    db: Annotated[AsyncSession, Depends(get_db)],
    secrets: Annotated[SecretsBackend, Depends(_secrets_dep)],
) -> StreamingResponse:
    """Run the agent loop and stream events to the client over SSE.

    The frontend consumes this with `fetch` + a ReadableStream reader (POST
    is needed because EventSource is GET-only).  Events emitted:
      - loop.started        — once, at the top
      - step.started        — per step
      - model.call.completed / .failed
      - tool.call.completed — per dispatched tool call
      - answer              — once, with the same payload as ChatResponseBody
    """
    logger.info(
        "chat_stream_request_received",
        tenant_id=str(ctx.tenant_id),
        user_id=str(ctx.user_id),
        question_chars=len(body.question),
    )

    provider = await get_model_for_tenant(ctx, _settings, secrets)
    capability = provider.capability()
    strategy = strategy_for(capability)
    connection = await get_wazuh_connection(ctx, db, secrets)
    knowledge_store = PgvectorKnowledgeStore(
        db, OllamaEmbeddingAdapter(_settings.ollama_base_url)
    )

    queue: asyncio.Queue[LoopEvent | None] = asyncio.Queue()

    async def emit(event: LoopEvent) -> None:
        await queue.put(event)

    async def runner() -> None:
        try:
            async with (
                WazuhOpenSearchClient(connection) as opensearch,
                WazuhServerApiClient(connection) as server_api,
            ):
                loop = AgentLoop(provider=provider, strategy=strategy)
                await loop.run(
                    question=body.question,
                    history=[(t.role, t.content) for t in body.history],
                    ctx=ctx,
                    db=db,
                    opensearch=opensearch,
                    server_api=server_api,
                    event_callback=emit,
                    knowledge_store=knowledge_store,
                )
            await db.commit()
        finally:
            await queue.put(None)  # sentinel

    async def event_stream() -> AsyncIterator[str]:
        task = asyncio.create_task(runner())
        try:
            while True:
                event = await queue.get()
                if event is None:
                    break
                yield _sse_format(event)
            yield "event: done\ndata: {}\n\n"
        finally:
            await task

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",  # disable proxy buffering (nginx)
        },
    )
