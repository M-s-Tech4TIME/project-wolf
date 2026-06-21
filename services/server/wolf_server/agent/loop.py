"""Core agent loop — plan-act-observe with bounded step budget.

The loop is provider- and strategy-agnostic.  It is given:
  - a ModelProvider (any adapter that satisfies the protocol)
  - a Strategy (frontier / guided / pipeline)
  - the request's OrganizationContext + DB + resolved Wazuh clients

It calls the model, dispatches any tool calls through the Phase 2A dispatcher,
feeds the structured results back, and terminates when the model returns a
final answer or the step budget is exhausted.

Every model call is audited (success or failure).  Every tool call is audited
inside the dispatcher.  Citations are aggregated across tool results so the
final answer can be traced end-to-end.

An optional `event_callback` lets a streaming consumer (the SSE chat endpoint)
observe every transition: loop start, each step, each model and tool call,
and the final answer.  Non-streaming callers omit the callback and see only
the AgentAnswer return value.
"""

import traceback
import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_common.errors import WolfError
from wolf_schema import ChatRequest, ChatResponse, ToolResult
from wolf_schema.chat import Message, MessageRole

from wolf_server.agent.events import EventCallback, LoopEvent, LoopEventType
from wolf_server.agent.prompts import RETRY_NUDGE
from wolf_server.agent.strategies import Strategy
from wolf_server.audit.log import write_event_from_context
from wolf_server.grounding import GroundingValidator, ValidationResult
from wolf_server.guardrails.limits import DEFAULT_LIMITS, ResourceLimits
from wolf_server.models.interface import (
    ChatStreamDelta,
    ChatStreamDone,
    ModelProvider,
)
from wolf_server.models.registry import registry as schema_registry
from wolf_server.organization.context import OrganizationContext
from wolf_server.tools.base import Citation
from wolf_server.tools.dispatcher import ToolDispatchResult, dispatch_tool_call
from wolf_server.wazuh.opensearch import WazuhOpenSearchClient
from wolf_server.wazuh.server_api import WazuhServerApiClient

logger = structlog.get_logger(__name__)

# Small local models (e.g. qwen3:4b) occasionally return an EMPTY final
# message right after consuming tool results — the work succeeded but no
# prose was emitted, surfacing to the user as a blank "(empty)" answer. When
# that happens we re-prompt once, WITHOUT tools, to force a written answer
# from the evidence already in the transcript. If even that comes back empty,
# we show an honest fallback instead of a blank bubble.
_SYNTHESIS_NUDGE = (
    "You already have all the information needed from the tool results above. "
    "Now write the final answer for the user in clear prose, based only on "
    "those results. Do not call any tools."
)
_EMPTY_ANSWER_FALLBACK = (
    "I gathered the data but wasn't able to compose a summary on this attempt. "
    "Please ask again or rephrase your question."
)


class AgentAnswer(BaseModel):
    """The final output of an agent loop run."""

    content: str
    citations: list[Citation] = []
    step_count: int
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    stop_reason: str  # "answer" | "budget_exhausted" | "loop_error"
    loop_id: str
    # Phase 3 Slice 2B — grounding validator counts. None if the validator
    # didn't run (no citations, empty answer, or judge call failed).
    grounding_supported: int | None = None
    grounding_unsupported: int | None = None
    grounding_uncertain: int | None = None
    grounding_unverifiable: int | None = None


async def _emit(
    callback: EventCallback | None,
    event_type: LoopEventType,
    data: dict[str, Any],
) -> None:
    if callback is not None:
        await callback(LoopEvent(type=event_type, data=data))


def _summarize_dispatch_result(result: ToolDispatchResult) -> dict[str, Any]:
    """Produce a small, JSON-serializable summary for an SSE event."""
    summary: dict[str, Any] = {
        "tool_name": result.tool_name,
        "tool_call_id": result.tool_call_id,
        "success": result.success,
        "elapsed_ms": result.elapsed_ms,
    }
    if result.error:
        summary["error"] = result.error
    if result.result:
        # Citation only — full payload would be too noisy for the SSE wire.
        citation = result.result.get("citation")
        if isinstance(citation, dict):
            summary["citation"] = citation
        # A few scalar counts for at-a-glance feedback.
        counts: dict[str, int] = {}
        for key, value in result.result.items():
            if isinstance(value, list):
                counts[f"{key}_count"] = len(value)
        if counts:
            summary["counts"] = counts
    return summary


@dataclass
class AgentLoop:
    """The plan-act-observe loop.  Construct one per chat request."""

    provider: ModelProvider
    strategy: Strategy
    limits: ResourceLimits = field(default_factory=lambda: DEFAULT_LIMITS)

    async def run(
        self,
        *,
        question: str,
        ctx: OrganizationContext,
        db: AsyncSession,
        opensearch: WazuhOpenSearchClient,
        server_api: WazuhServerApiClient,
        history: list[tuple[str, str]] | None = None,
        event_callback: EventCallback | None = None,
        knowledge_store: Any | None = None,
        grounding_validator: GroundingValidator | None = None,
        grounding_mode: str = "blocking",
        cache: Any | None = None,
        retry_nudge: bool = False,
    ) -> AgentAnswer:
        capability = self.provider.capability()
        budget = self.strategy.step_budget(capability)
        tools = self.strategy.model_tools(schema_registry.model_tools())

        loop_id = uuid.uuid4().hex
        messages: list[Message] = [
            Message(role=MessageRole.system, content=self.strategy.system_prompt()),
        ]
        # Replay prior user/assistant turns so follow-up questions have
        # context.  Only role+content is replayed; we do not re-execute
        # past tool calls because their results may be stale.
        for role_str, content in history or []:
            role = MessageRole.user if role_str == "user" else MessageRole.assistant
            messages.append(Message(role=role, content=content))
        # Slice 5.0c-g: the analyst-side Retry chip on a Wolf response
        # re-submits the original question with retry_nudge=True.
        # wolf-dashboard includes the previous Q→A pair in history, so
        # the model has its previous attempt to compare against.
        effective_question = f"{question}\n\n{RETRY_NUDGE}" if retry_nudge else question
        messages.append(Message(role=MessageRole.user, content=effective_question))
        citations: list[Citation] = []
        # Per-call evidence accumulators for the Slice-2B grounding validator.
        # Knowledge chunks come from query_runbook's `hits`; everything else
        # is a tool result. Both surface in the validator's evidence prompt
        # with appropriate provenance tags.
        all_tool_results: list[dict[str, Any]] = []
        all_retrieved_chunks: list[dict[str, Any]] = []
        # Failed tool calls (Slice 5.0b): surfaced to the grounding validator
        # as negative evidence so fabricated specifics that should have come
        # from a failed tool are flagged unsupported.
        all_tool_failures: list[dict[str, Any]] = []
        total_input_tokens = 0
        total_output_tokens = 0
        tool_call_count = 0

        logger.info(
            "agent_loop_started",
            loop_id=loop_id,
            organization_id=str(ctx.organization_id),
            strategy=self.strategy.name,
            model_id=capability.model_id,
            step_budget=budget,
            tool_catalog_size=len(tools),
        )
        await _emit(
            event_callback,
            "loop.started",
            {
                "loop_id": loop_id,
                "strategy": self.strategy.name,
                "model_id": capability.model_id,
                "provider": capability.provider,
                "step_budget": budget,
            },
        )

        for step in range(budget):
            await _emit(event_callback, "step.started", {"step": step})
            request = ChatRequest(messages=messages, tools=tools or None)

            try:
                response = await self._chat_or_stream(
                    request,
                    step=step,
                    event_callback=event_callback,
                )
            except WolfError:
                raise
            except Exception as exc:
                # Capture both the type and message — many httpx exceptions
                # have an empty str() and were silently logging as just
                # "Model call failed:" with no detail.  Persist the
                # traceback into the audit record so the next occurrence
                # is forensically recoverable.
                exc_type = type(exc).__name__
                exc_msg = str(exc) or "(no message)"
                detail = f"{exc_type}: {exc_msg}"
                tb = "".join(traceback.format_exception(exc))
                logger.error(
                    "agent_loop_model_call_failed",
                    loop_id=loop_id,
                    exc_type=exc_type,
                    detail=detail,
                    traceback=tb,
                )
                await self._audit_model_failure(
                    db,
                    ctx,
                    loop_id,
                    step,
                    detail,
                    traceback=tb,
                )
                await _emit(
                    event_callback,
                    "model.call.failed",
                    {
                        "step": step,
                        "detail": detail,
                    },
                )
                answer = AgentAnswer(
                    content=f"Model call failed ({exc_type}): {exc_msg}",
                    citations=citations,
                    step_count=step,
                    tool_call_count=tool_call_count,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    stop_reason="loop_error",
                    loop_id=loop_id,
                )
                await _emit(event_callback, "answer", answer.model_dump(mode="json"))
                return answer

            total_input_tokens += response.input_tokens
            total_output_tokens += response.output_tokens
            await self._audit_model_success(db, ctx, loop_id, step, response)
            await _emit(
                event_callback,
                "model.call.completed",
                {
                    "step": step,
                    "input_tokens": response.input_tokens,
                    "output_tokens": response.output_tokens,
                    "stop_reason": response.stop_reason,
                    "tool_call_count": len(response.tool_calls),
                },
            )

            messages.append(
                Message(
                    role=MessageRole.assistant,
                    content=response.content,
                    tool_calls=response.tool_calls or None,
                )
            )

            if not response.tool_calls:
                final_content = response.content
                if not final_content.strip():
                    # Empty completion after a real run — re-prompt once
                    # (no tools) to coax the written answer out of the model.
                    retry = await self._synthesize_final(messages, loop_id=loop_id)
                    if retry is not None:
                        total_input_tokens += retry.input_tokens
                        total_output_tokens += retry.output_tokens
                        if retry.content.strip():
                            final_content = retry.content
                    if not final_content.strip():
                        final_content = _EMPTY_ANSWER_FALLBACK
                logger.info(
                    "agent_loop_completed",
                    loop_id=loop_id,
                    stop_reason="answer",
                    steps=step + 1,
                    tool_calls=tool_call_count,
                    empty_recovered=not response.content.strip(),
                )
                answer = AgentAnswer(
                    content=final_content,
                    citations=citations,
                    step_count=step + 1,
                    tool_call_count=tool_call_count,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    stop_reason="answer",
                    loop_id=loop_id,
                )
                return await self._finalize_answer(
                    answer,
                    validator=grounding_validator,
                    tool_results=all_tool_results,
                    retrieved_chunks=all_retrieved_chunks,
                    tool_failures=all_tool_failures,
                    db=db,
                    ctx=ctx,
                    event_callback=event_callback,
                    mode=grounding_mode,
                )

            tool_results: list[ToolResult] = []
            for call in response.tool_calls:
                tool_call_count += 1
                # Live activity feed (Slice 5.0c-e): announce the tool
                # BEFORE dispatch so the UI can narrate "Searching Wazuh
                # for …" instead of staying on the previous status line
                # for the whole tool call.
                await _emit(
                    event_callback,
                    "tool.call.started",
                    {
                        "tool_name": call.name,
                        "tool_call_id": call.id,
                        "arguments": call.arguments,
                    },
                )
                dispatch_result = await dispatch_tool_call(
                    call,
                    ctx=ctx,
                    db=db,
                    opensearch=opensearch,
                    server_api=server_api,
                    limits=self.limits,
                    knowledge_store=knowledge_store,
                    cache=cache,
                )
                await _emit(
                    event_callback,
                    "tool.call.completed",
                    _summarize_dispatch_result(dispatch_result),
                )
                if dispatch_result.success and dispatch_result.result:
                    citation = dispatch_result.result.get("citation")
                    if isinstance(citation, dict):
                        citations.append(Citation.model_validate(citation))
                    # Validator evidence — split query_runbook hits out as
                    # knowledge chunks; everything else stays as a tool
                    # result. Better provenance in the judge's prompt.
                    if call.name == "query_runbook":
                        for hit in dispatch_result.result.get("hits", []):
                            if isinstance(hit, dict):
                                all_retrieved_chunks.append(hit)
                    else:
                        all_tool_results.append(
                            {
                                "name": call.name,
                                "content": dispatch_result.result,
                            }
                        )
                    tool_results.append(
                        ToolResult(
                            tool_call_id=call.id,
                            name=call.name,
                            content=dispatch_result.result,
                        )
                    )
                else:
                    error_msg = dispatch_result.error or "tool call failed"
                    all_tool_failures.append({"name": call.name, "error": error_msg})
                    tool_results.append(
                        ToolResult(
                            tool_call_id=call.id,
                            name=call.name,
                            content="",
                            error=error_msg,
                        )
                    )

            messages.append(Message(role=MessageRole.tool, tool_results=tool_results))

        # Budget exhausted without final answer.
        logger.warning(
            "agent_loop_budget_exhausted",
            loop_id=loop_id,
            steps=budget,
            tool_calls=tool_call_count,
        )
        last_assistant = next(
            (m.content for m in reversed(messages) if m.role == MessageRole.assistant),
            "",
        )
        answer = AgentAnswer(
            content=last_assistant
            or "The step budget was exhausted before I could complete the investigation. "
            "Please narrow your question or try again.",
            citations=citations,
            step_count=budget,
            tool_call_count=tool_call_count,
            input_tokens=total_input_tokens,
            output_tokens=total_output_tokens,
            stop_reason="budget_exhausted",
            loop_id=loop_id,
        )
        return await self._finalize_answer(
            answer,
            validator=grounding_validator,
            tool_results=all_tool_results,
            retrieved_chunks=all_retrieved_chunks,
            tool_failures=all_tool_failures,
            db=db,
            ctx=ctx,
            event_callback=event_callback,
            mode=grounding_mode,
        )

    # ── Recovery + audit helpers ──────────────────────────────────────────

    async def _chat_or_stream(
        self,
        request: ChatRequest,
        *,
        step: int,
        event_callback: EventCallback | None,
    ) -> ChatResponse:
        """Streaming model call when the provider supports it; blocking
        fallback otherwise (Slice 5.0c-d).

        For each token delta from the provider, emits a ``model.delta``
        SSE event so wolf-dashboard can render the answer progressively
        instead of waiting for the whole response. The fully-assembled
        :class:`ChatResponse` is returned unchanged — every downstream
        consumer (tool-call dispatch, token accounting, _finalize_answer)
        sees the exact same shape it did before.

        Providers that haven't implemented ``chat_stream`` yet (currently
        OpenAI and Anthropic) fall through to the blocking ``chat()``
        call. They don't break — they just don't get progressive UI.
        """
        chat_stream = getattr(self.provider, "chat_stream", None)
        if chat_stream is None:
            return await self.provider.chat(request)

        response: ChatResponse | None = None
        async for event in chat_stream(request):
            if isinstance(event, ChatStreamDelta):
                if event.content_delta:
                    await _emit(
                        event_callback,
                        "model.delta",
                        {
                            "step": step,
                            "content_delta": event.content_delta,
                        },
                    )
            elif isinstance(event, ChatStreamDone):
                response = event.response
        if response is None:
            raise RuntimeError(
                "chat_stream completed without emitting a ChatStreamDone "
                "event — provider contract violation"
            )
        return response

    async def _synthesize_final(
        self, messages: list[Message], *, loop_id: str
    ) -> ChatResponse | None:
        """Re-prompt once (no tools) to recover from an empty final answer.

        Returns the retry response, or None if the call itself failed. The
        caller decides whether the recovered content is usable. Never raises —
        recovery is best-effort; a failure just leaves the fallback message.
        """
        try:
            nudge = [*messages, Message(role=MessageRole.user, content=_SYNTHESIS_NUDGE)]
            # tools omitted → the model must write prose, not call a tool.
            return await self.provider.chat(ChatRequest(messages=nudge))
        except Exception as exc:
            logger.warning(
                "agent_loop_synthesis_retry_failed",
                loop_id=loop_id,
                exc_type=type(exc).__name__,
            )
            return None

    async def _finalize_answer(
        self,
        answer: AgentAnswer,
        *,
        validator: GroundingValidator | None,
        tool_results: list[dict[str, Any]],
        retrieved_chunks: list[dict[str, Any]],
        tool_failures: list[dict[str, Any]] | None = None,
        db: AsyncSession,
        ctx: OrganizationContext,
        event_callback: EventCallback | None,
        mode: str = "blocking",
    ) -> AgentAnswer:
        """Run the grounding validator and emit the `answer` + grounding SSE
        events per the configured execution mode (ADR 0026).

        Always emits exactly one `answer` event and returns an AgentAnswer
        (annotated + counted when grounding ran; the original otherwise).

          - blocking     — judge awaited, THEN the `answer` event (annotated).
          - deferred     — `answer` event first (raw + `grounding_pending`),
                           judge runs, then `grounding.completed` carries the
                           annotated content + counts to patch the settled
                           message.
          - incremental  — like deferred, but claims are judged in concurrent
                           batches and each batch emits a `grounding.partial`.

        When the validator isn't configured / the answer is empty / there is
        no evidence, the `answer` event goes out unchanged and the original
        answer is returned.
        """
        tool_failures = tool_failures or []
        # Streaming-only modes are meaningless without an event sink; the
        # non-streaming POST /chat path always runs blocking semantics (it
        # returns one payload). ADR 0026.
        if event_callback is None:
            mode = "blocking"

        grounds = (
            validator is not None
            and bool(answer.content.strip())
            # No successful tools/chunks AND nothing failed → nothing to verify
            # against (doc 06). But a FAILED tool (Slice 5.0b) IS validated —
            # that is exactly where the model fabricates to fill the gap.
            and (bool(answer.citations) or bool(tool_failures))
        )
        if not grounds:
            await _emit(event_callback, "answer", answer.model_dump(mode="json"))
            return answer
        assert validator is not None  # narrowed by `grounds`

        # deferred / incremental: settle the answer in the UI immediately
        # (raw + pending) so time-to-readable-answer is the token stream alone.
        if mode in ("deferred", "incremental"):
            prelim = answer.model_dump(mode="json")
            prelim["grounding_pending"] = True
            await _emit(event_callback, "answer", prelim)

        # Live activity feed (Slice 5.0c-e): announce grounding before the
        # (potentially multi-minute) judge call so the UI doesn't look stuck.
        await _emit(
            event_callback,
            "grounding.started",
            {
                "loop_id": answer.loop_id,
                "claim_count_estimate": len(answer.content.split(".")),
            },
        )

        validation: ValidationResult | None = None
        if mode == "incremental":
            # Concurrent batched judging; emit one partial per completed batch
            # so chips pop in progressively. The last snapshot is complete.
            async for snapshot in validator.validate_streaming(
                answer.content,
                tool_results=tool_results,
                retrieved_chunks=retrieved_chunks,
                tool_failures=tool_failures,
                loop_id=answer.loop_id,
            ):
                validation = snapshot
                await _emit(
                    event_callback,
                    "grounding.partial",
                    {
                        "loop_id": answer.loop_id,
                        "ran": snapshot.ran,
                        "supported": snapshot.supported_count,
                        "unsupported": snapshot.unsupported_count,
                        "uncertain": snapshot.uncertain_count,
                        "unverifiable": snapshot.unverifiable_count,
                        "annotated_content": snapshot.annotated_answer,
                    },
                )
        else:  # blocking + deferred share the single-call judge path
            validation = await validator.validate(
                answer.content,
                tool_results=tool_results,
                retrieved_chunks=retrieved_chunks,
                tool_failures=tool_failures,
                loop_id=answer.loop_id,
            )

        ran = validation is not None and validation.ran

        await write_event_from_context(
            db,
            ctx,
            event_type="grounding.validation.completed",
            event_data={
                "loop_id": answer.loop_id,
                "mode": mode,
                "ran": ran,
                "supported": validation.supported_count if validation else 0,
                "unsupported": validation.unsupported_count if validation else 0,
                "uncertain": validation.uncertain_count if validation else 0,
                "unverifiable": validation.unverifiable_count if validation else 0,
                "total_claims": len(validation.claims) if validation else 0,
            },
        )

        completed: dict[str, Any] = {
            "loop_id": answer.loop_id,
            "ran": ran,
            "supported": validation.supported_count if validation else 0,
            "unsupported": validation.unsupported_count if validation else 0,
            "uncertain": validation.uncertain_count if validation else 0,
            "unverifiable": validation.unverifiable_count if validation else 0,
        }
        # In deferred/incremental the annotated content rides the final
        # grounding event to patch the already-settled message; in blocking it
        # rides the `answer` event emitted below.
        if mode in ("deferred", "incremental") and validation is not None:
            completed["annotated_content"] = validation.annotated_answer
        await _emit(event_callback, "grounding.completed", completed)

        final = (
            answer.model_copy(
                update={
                    "content": validation.annotated_answer,
                    "grounding_supported": validation.supported_count,
                    "grounding_unsupported": validation.unsupported_count,
                    "grounding_uncertain": validation.uncertain_count,
                    "grounding_unverifiable": validation.unverifiable_count,
                }
            )
            if ran and validation is not None
            else answer
        )
        if mode == "blocking":
            await _emit(event_callback, "answer", final.model_dump(mode="json"))
        return final

    async def _audit_model_success(
        self,
        db: AsyncSession,
        ctx: OrganizationContext,
        loop_id: str,
        step: int,
        response: ChatResponse,
    ) -> None:
        await write_event_from_context(
            db,
            ctx,
            event_type="model.call.success",
            event_data={
                "loop_id": loop_id,
                "step": step,
                "model_id": response.model_id,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "stop_reason": response.stop_reason,
                "tool_call_count": len(response.tool_calls),
                "provider": self.provider.capability().provider,
                "strategy": self.strategy.name,
            },
        )

    async def _audit_model_failure(
        self,
        db: AsyncSession,
        ctx: OrganizationContext,
        loop_id: str,
        step: int,
        detail: str,
        traceback: str | None = None,
    ) -> None:
        event_data: dict[str, Any] = {
            "loop_id": loop_id,
            "step": step,
            "detail": detail[:1000],
            "provider": self.provider.capability().provider,
        }
        if traceback:
            # Truncate to keep audit rows reasonable but long enough that
            # the relevant frames survive.
            event_data["traceback"] = traceback[:4000]
        await write_event_from_context(
            db,
            ctx,
            event_type="model.call.failure",
            event_data=event_data,
        )
