"""Core agent loop — plan-act-observe, unbounded persistence (6-f.5).

The loop is provider- and strategy-agnostic.  It is given:
  - a ModelProvider (any adapter that satisfies the protocol)
  - a Strategy (frontier / guided / pipeline)
  - the request's OrganizationContext + DB + resolved Wazuh clients

It calls the model, dispatches any tool calls through the Phase 2A dispatcher,
feeds the structured results back, and terminates when the model returns a
final answer.  There is NO fixed step ceiling (operator directive 2026-07-06:
"utilize the step count, but never limit it to any specific value") — the loop
persists until the model is satisfied, and the only stops besides a real
answer are grounded in actual progress, not a number:

  - the no-progress guard (a whole step of exact-repeat tool calls, twice) —
    the model is looping, more steps cannot help;
  - the context-fit guard — the transcript is about to outgrow the model's
    effective context window, so further gathering physically cannot fit;
  - an OPTIONAL operator circuit breaker (``AGENT_STEP_BREAKER``, default
    off) for cost protection on paid APIs.

EVERY such stop ends in a forced best-effort SYNTHESIS from the evidence
already gathered — never a canned "budget exhausted" apology.  The model's
graded ``max_safe_autonomous_steps`` survives as a soft CHECKPOINT: at that
cadence the loop nudges the model to take stock (answer if it can, refocus if
it can't), which utilizes the grading without walling on it.

Every model call is audited (success or failure).  Every tool call is audited
inside the dispatcher.  Citations are aggregated across tool results so the
final answer can be traced end-to-end.

An optional `event_callback` lets a streaming consumer (the SSE chat endpoint)
observe every transition: loop start, each step, each model and tool call,
and the final answer.  Non-streaming callers omit the callback and see only
the AgentAnswer return value.
"""

import json
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
from wolf_server.agent.prompts import RETRY_NUDGE, WEB_RESEARCH_SUFFIX
from wolf_server.agent.strategies import Strategy
from wolf_server.audit.log import write_event_from_context
from wolf_server.grounding import GroundingValidator, ValidationResult
from wolf_server.guardrails.limits import DEFAULT_LIMITS, ResourceLimits
from wolf_server.models.interface import (
    ChatStreamDelta,
    ChatStreamDone,
    ModelProvider,
)
from wolf_server.models.openai import (
    ModelProviderPaymentRequiredError,
    ModelProviderRateLimitError,
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

# Repeated-tool-call guard (2026-07-01). Weaker models sometimes loop on the
# same tool call — e.g. calling query_runbook with the same args over and over —
# never synthesizing (a bad, expensive UX: 200 K+ tokens for no answer). When a
# whole step's tool calls are all EXACT repeats of earlier calls, we nudge the
# model to answer from what it has; after two consecutive redundant steps we
# force a final synthesis. Since 6-f.5 this is the loop's primary stop besides
# a real answer — it detects NO PROGRESS, which is the honest reason to stop
# (a step count never was). This is model-agnostic — a strong model never
# trips it; a looping one is stopped.
_REDUNDANT_TOOL_NUDGE = (
    "You have already run these exact queries and their results are in the "
    "transcript above — running them again returns the same data. Stop searching "
    "and answer the user's question now using the information you already have. "
    "If a specific detail is genuinely missing, say what it is instead of "
    "repeating a search."
)
_MAX_REDUNDANT_STREAK = 2

# Soft checkpoint (6-f.5): at every `max_safe_autonomous_steps` boundary the
# loop asks the model to take stock — answer if the evidence suffices, refocus
# if not. A nudge, NEVER a wall: the loop keeps going as long as the model
# makes real progress.
_CHECKPOINT_NUDGE = (
    "Checkpoint — you have taken several investigation steps. Take stock: if "
    "the evidence gathered above already answers the user's question, write "
    "the final answer now. If something specific is still missing, name it to "
    "yourself and continue — focused on retrieving exactly that."
)

# Forced-synthesis stops (6-f.5): when the loop must stop without the model
# volunteering an answer (no progress / context full / operator breaker), it
# re-prompts once WITHOUT tools to compose the best possible answer from the
# evidence already gathered — honestly noting gaps — instead of returning a
# canned failure.
_FORCED_SYNTHESIS_NUDGE = (
    "Stop investigating now and write the best possible final answer from the "
    "evidence already gathered above. Be direct and complete about what the "
    "evidence supports; if something the user asked for could not be "
    "established, say plainly what is missing and what you would check next. "
    "Do not call any tools."
)


class AgentAnswer(BaseModel):
    """The final output of an agent loop run."""

    content: str
    citations: list[Citation] = []
    step_count: int
    tool_call_count: int
    input_tokens: int
    output_tokens: int
    # "answer" | "loop_error" — "budget_exhausted" is gone since 6-f.5 (no
    # fixed step ceiling exists; forced-synthesis stops are real answers).
    stop_reason: str
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


def _model_failure_message(exc: Exception) -> str:
    """A readable, non-leaky answer body for a failed model call.

    Shown to the analyst in the chat when the model provider errors. Keeps the
    signal (rate-limit vs other provider error) without dumping raw provider
    JSON or a traceback into the conversation.
    """
    # Quota/credit exhaustion (429/402) carries a normalized ProviderQuota with
    # the LIVE remaining count + reset time — surface that actionable detail
    # (add credits / wait Nh / use a local model) instead of a hardcoded cap.
    if isinstance(exc, ModelProviderRateLimitError | ModelProviderPaymentRequiredError):
        quota = getattr(exc, "quota", None)
        if quota is not None:
            return f"Wolf couldn't complete this request — {quota.user_message()}"
    if isinstance(exc, ModelProviderRateLimitError):
        return (
            "Wolf couldn't complete this request — the model provider is "
            "rate-limited right now (HTTP 429). This is common on free-tier "
            "models; please try again in a moment, or switch to a local model."
        )
    if isinstance(exc, ModelProviderPaymentRequiredError):
        return (
            "Wolf couldn't complete this request — the model provider requires "
            "payment (HTTP 402), usually a depleted credit balance. Add credits "
            "or switch this organization back to a local model."
        )
    if isinstance(exc, WolfError):
        msg = (str(exc) or "").strip()
        short = f"{msg[:200]}…" if len(msg) > 200 else msg
        tail = f" ({short})" if short else ""
        return (
            "Wolf couldn't complete this request — the model provider returned "
            f"an error{tail}. Please try again; if it persists, check the model "
            "configuration."
        )
    return (
        f"Wolf couldn't complete this request — the model call failed "
        f"({type(exc).__name__}). Please try again."
    )


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
    # 6-f.5 persistence knobs (wired from Settings by the chat API; the
    # defaults here mirror the Settings defaults for direct construction).
    # 0 = no operator circuit breaker — unbounded persistence.
    step_breaker: int = 0
    # Transcript share of the model's effective context window at which the
    # loop stops gathering and synthesizes from what it has.
    context_fit_threshold: float = 0.8

    def _effective_context_window(self) -> int:
        """The context the provider will actually serve — the adapter's own
        effective window when it exposes one (Ollama's loaded ``num_ctx`` is
        typically far below the model family's nominal window), else the
        capability descriptor's nominal window."""
        fn = getattr(self.provider, "effective_context_window", None)
        if callable(fn):
            return int(fn())
        return int(self.provider.capability().context_window)

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
        research: Any | None = None,
    ) -> AgentAnswer:
        capability = self.provider.capability()
        # 6-f.5: the strategy's step budget is a soft CHECKPOINT cadence (take
        # stock, answer if you can), never a wall — see the module docstring.
        checkpoint_every = max(1, self.strategy.step_budget(capability))
        context_fit_limit = int(self._effective_context_window() * self.context_fit_threshold)
        tools = self.strategy.model_tools(schema_registry.model_tools())

        loop_id = uuid.uuid4().hex
        # The web-research teaching rides the system prompt ONLY when the
        # request actually has a ResearchContext (ADR 0032 — tools are
        # registration-gated; never teach tools the model may not have).
        system_prompt = self.strategy.system_prompt()
        if research is not None:
            system_prompt += WEB_RESEARCH_SUFFIX
        messages: list[Message] = [
            Message(role=MessageRole.system, content=system_prompt),
        ]
        # Replay prior user/assistant turns so follow-up questions have
        # context.  Only role+content is replayed; we do not re-execute
        # past tool calls because their results may be stale.
        #
        # Skip empty/whitespace turns: an INTERRUPTED prior turn ("no response
        # generated before stop") is stored with empty content, and replaying
        # it as {"role":"assistant","content":""} makes strict providers (e.g.
        # Cohere via OpenRouter) reject the whole request with 400 "invalid
        # message" — a step-0 hard fail. An empty turn carries no context
        # anyway, so dropping it is both a bug fix and semantically correct.
        for role_str, content in history or []:
            if not content or not content.strip():
                continue
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
        # Repeated-tool-call guard: signature = name + sorted args. Counts how
        # often each exact call has been made so a step that only repeats prior
        # calls can be detected and stopped (see _REDUNDANT_TOOL_NUDGE).
        tool_signatures: dict[str, int] = {}
        redundant_streak = 0

        logger.info(
            "agent_loop_started",
            loop_id=loop_id,
            organization_id=str(ctx.organization_id),
            strategy=self.strategy.name,
            model_id=capability.model_id,
            checkpoint_every=checkpoint_every,
            step_breaker=self.step_breaker,
            context_fit_limit=context_fit_limit,
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
            },
        )

        step = -1
        while True:
            step += 1
            await _emit(event_callback, "step.started", {"step": step})
            request = ChatRequest(messages=messages, tools=tools or None)

            try:
                response = await self._chat_or_stream(
                    request,
                    step=step,
                    event_callback=event_callback,
                )
            except WolfError as exc:
                # Blocking POST /chat (no event_callback): re-raise so the API
                # layer maps the WolfError to a clean HTTP error response.
                # Streaming POST /chat/stream: the SSE response has ALREADY
                # started, so a raise here would break the stream ("response
                # already started") and leave the browser hanging on
                # "thinking…" forever (with Stop dead). Instead, emit a clean
                # terminal failure + answer so the UI settles into an honest
                # error. This is provider-agnostic — a 429/400/404/timeout from
                # ANY model degrades gracefully.
                if event_callback is None:
                    raise
                return await self._fail_gracefully(
                    exc,
                    db=db,
                    ctx=ctx,
                    loop_id=loop_id,
                    step=step,
                    citations=citations,
                    tool_call_count=tool_call_count,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    event_callback=event_callback,
                )
            except Exception as exc:
                return await self._fail_gracefully(
                    exc,
                    db=db,
                    ctx=ctx,
                    loop_id=loop_id,
                    step=step,
                    citations=citations,
                    tool_call_count=tool_call_count,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    event_callback=event_callback,
                )

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
            step_had_new_call = False
            for call in response.tool_calls:
                tool_call_count += 1
                # Repeated-tool-call guard: a step is "redundant" only if EVERY
                # call in it exactly repeats an earlier call (same name + args).
                signature = f"{call.name}:{json.dumps(call.arguments, sort_keys=True, default=str)}"
                if tool_signatures.get(signature, 0) == 0:
                    step_had_new_call = True
                tool_signatures[signature] = tool_signatures.get(signature, 0) + 1
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
                    research=research,
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
                    # Web tools emit one citation PER result/page (ADR 0032
                    # A1/A5) via a plural `citations` list.
                    for cited in dispatch_result.result.get("citations") or []:
                        if isinstance(cited, dict):
                            citations.append(Citation.model_validate(cited))
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

            # Repeated-tool-call guard (2026-07-01): if this whole step only
            # repeated earlier calls, the model is looping. Nudge it to answer;
            # after _MAX_REDUNDANT_STREAK consecutive redundant steps, force a
            # final synthesis (no tools) — more steps cannot produce new
            # evidence, so this is the honest stop (6-f.5's primary guard).
            if step_had_new_call:
                redundant_streak = 0
            else:
                redundant_streak += 1
                logger.info(
                    "agent_loop_redundant_tool_step",
                    loop_id=loop_id,
                    step=step,
                    redundant_streak=redundant_streak,
                )
                if redundant_streak >= _MAX_REDUNDANT_STREAK:
                    return await self._synthesized_stop(
                        cause="no_progress",
                        messages=messages,
                        loop_id=loop_id,
                        steps_done=step + 1,
                        tool_call_count=tool_call_count,
                        total_input_tokens=total_input_tokens,
                        total_output_tokens=total_output_tokens,
                        citations=citations,
                        grounding_validator=grounding_validator,
                        all_tool_results=all_tool_results,
                        all_retrieved_chunks=all_retrieved_chunks,
                        all_tool_failures=all_tool_failures,
                        db=db,
                        ctx=ctx,
                        event_callback=event_callback,
                        grounding_mode=grounding_mode,
                    )
                messages.append(Message(role=MessageRole.user, content=_REDUNDANT_TOOL_NUDGE))

            # 6-f.5 persistence guards — each grounded in something real (an
            # operator's explicit breaker; the physical context window), never
            # a hardcoded step number.
            steps_done = step + 1
            if self.step_breaker > 0 and steps_done >= self.step_breaker:
                logger.warning(
                    "agent_loop_step_breaker_tripped",
                    loop_id=loop_id,
                    steps=steps_done,
                    breaker=self.step_breaker,
                )
                return await self._synthesized_stop(
                    cause="step_breaker",
                    messages=messages,
                    loop_id=loop_id,
                    steps_done=steps_done,
                    tool_call_count=tool_call_count,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    citations=citations,
                    grounding_validator=grounding_validator,
                    all_tool_results=all_tool_results,
                    all_retrieved_chunks=all_retrieved_chunks,
                    all_tool_failures=all_tool_failures,
                    db=db,
                    ctx=ctx,
                    event_callback=event_callback,
                    grounding_mode=grounding_mode,
                )
            if response.input_tokens >= context_fit_limit:
                logger.warning(
                    "agent_loop_context_fit_stop",
                    loop_id=loop_id,
                    steps=steps_done,
                    input_tokens=response.input_tokens,
                    context_fit_limit=context_fit_limit,
                )
                return await self._synthesized_stop(
                    cause="context_full",
                    messages=messages,
                    loop_id=loop_id,
                    steps_done=steps_done,
                    tool_call_count=tool_call_count,
                    total_input_tokens=total_input_tokens,
                    total_output_tokens=total_output_tokens,
                    citations=citations,
                    grounding_validator=grounding_validator,
                    all_tool_results=all_tool_results,
                    all_retrieved_chunks=all_retrieved_chunks,
                    all_tool_failures=all_tool_failures,
                    db=db,
                    ctx=ctx,
                    event_callback=event_callback,
                    grounding_mode=grounding_mode,
                )
            # Soft checkpoint: utilize the model's graded step comfort zone as
            # a take-stock cadence — never a wall.
            if steps_done % checkpoint_every == 0:
                messages.append(Message(role=MessageRole.user, content=_CHECKPOINT_NUDGE))

    async def _synthesized_stop(
        self,
        *,
        cause: str,
        messages: list[Message],
        loop_id: str,
        steps_done: int,
        tool_call_count: int,
        total_input_tokens: int,
        total_output_tokens: int,
        citations: list[Citation],
        grounding_validator: GroundingValidator | None,
        all_tool_results: list[dict[str, Any]],
        all_retrieved_chunks: list[dict[str, Any]],
        all_tool_failures: list[dict[str, Any]],
        db: AsyncSession,
        ctx: OrganizationContext,
        event_callback: EventCallback | None,
        grounding_mode: str,
    ) -> AgentAnswer:
        """Force a best-effort final synthesis (no tools) and finalize it.

        Every stop that isn't the model volunteering an answer lands here — no
        progress, context full, operator breaker.  The result is a REAL answer
        composed from the gathered evidence (stop_reason="answer", honest about
        gaps via the synthesis nudge), never a canned failure (6-f.5)."""
        forced = await self._synthesize_final(
            messages, loop_id=loop_id, nudge=_FORCED_SYNTHESIS_NUDGE
        )
        if forced is not None:
            total_input_tokens += forced.input_tokens
            total_output_tokens += forced.output_tokens
        forced_content = (forced.content if forced else "") or ""
        if not forced_content.strip():
            forced_content = (
                next(
                    (
                        m.content
                        for m in reversed(messages)
                        if m.role == MessageRole.assistant and m.content
                    ),
                    "",
                )
                or _EMPTY_ANSWER_FALLBACK
            )
        logger.info(
            "agent_loop_completed",
            loop_id=loop_id,
            stop_reason="answer",
            steps=steps_done,
            tool_calls=tool_call_count,
            forced_synthesis=cause,
        )
        answer = AgentAnswer(
            content=forced_content,
            citations=citations,
            step_count=steps_done,
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
        self, messages: list[Message], *, loop_id: str, nudge: str = _SYNTHESIS_NUDGE
    ) -> ChatResponse | None:
        """Re-prompt once (no tools) to coax a written answer from the evidence.

        Used to recover an empty final answer (default nudge) and to compose
        the best-effort answer on a forced stop (``_FORCED_SYNTHESIS_NUDGE``).
        Returns the retry response, or None if the call itself failed. The
        caller decides whether the recovered content is usable. Never raises —
        recovery is best-effort; a failure just leaves the fallback message.
        """
        try:
            prompted = [*messages, Message(role=MessageRole.user, content=nudge)]
            # tools omitted → the model must write prose, not call a tool.
            return await self.provider.chat(ChatRequest(messages=prompted))
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

    async def _fail_gracefully(
        self,
        exc: Exception,
        *,
        db: AsyncSession,
        ctx: OrganizationContext,
        loop_id: str,
        step: int,
        citations: list[Citation],
        tool_call_count: int,
        total_input_tokens: int,
        total_output_tokens: int,
        event_callback: EventCallback | None,
    ) -> "AgentAnswer":
        """Turn a model-call failure into a clean terminal answer + events.

        Logs + audits the failure with a traceback, emits `model.call.failed`
        then a settled `answer` event (so streaming clients leave the running
        state instead of hanging), and returns an AgentAnswer carrying a
        readable, non-leaky message. Shared by the WolfError (streaming) and
        the generic-exception paths so both degrade identically.
        """
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
        await self._audit_model_failure(db, ctx, loop_id, step, detail, traceback=tb)
        await _emit(
            event_callback,
            "model.call.failed",
            {"step": step, "detail": detail},
        )
        answer = AgentAnswer(
            content=_model_failure_message(exc),
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
