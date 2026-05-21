"""Core agent loop — plan-act-observe with bounded step budget.

The loop is provider- and strategy-agnostic.  It is given:
  - a ModelProvider (any adapter that satisfies the protocol)
  - a Strategy (frontier / guided / pipeline)
  - the request's TenantContext + DB + resolved Wazuh clients

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

import uuid
from dataclasses import dataclass, field
from typing import Any

import structlog
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_common.errors import WolfError
from wolf_schema import ChatRequest, ChatResponse, ToolResult
from wolf_schema.chat import Message, MessageRole

from app.agent.events import EventCallback, LoopEvent, LoopEventType
from app.agent.strategies import Strategy
from app.audit.log import write_event_from_context
from app.guardrails.limits import DEFAULT_LIMITS, ResourceLimits
from app.models.interface import ModelProvider
from app.models.registry import registry as schema_registry
from app.tenancy.context import TenantContext
from app.tools.base import Citation
from app.tools.dispatcher import ToolDispatchResult, dispatch_tool_call
from app.wazuh.opensearch import WazuhOpenSearchClient
from app.wazuh.server_api import WazuhServerApiClient

logger = structlog.get_logger(__name__)


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
        ctx: TenantContext,
        db: AsyncSession,
        opensearch: WazuhOpenSearchClient,
        server_api: WazuhServerApiClient,
        event_callback: EventCallback | None = None,
    ) -> AgentAnswer:
        capability = self.provider.capability()
        budget = self.strategy.step_budget(capability)
        tools = self.strategy.model_tools(schema_registry.model_tools())

        loop_id = uuid.uuid4().hex
        messages: list[Message] = [
            Message(role=MessageRole.system, content=self.strategy.system_prompt()),
            Message(role=MessageRole.user, content=question),
        ]
        citations: list[Citation] = []
        total_input_tokens = 0
        total_output_tokens = 0
        tool_call_count = 0

        logger.info(
            "agent_loop_started",
            loop_id=loop_id,
            tenant_id=str(ctx.tenant_id),
            strategy=self.strategy.name,
            model_id=capability.model_id,
            step_budget=budget,
            tool_catalog_size=len(tools),
        )
        await _emit(event_callback, "loop.started", {
            "loop_id": loop_id,
            "strategy": self.strategy.name,
            "model_id": capability.model_id,
            "provider": capability.provider,
            "step_budget": budget,
        })

        for step in range(budget):
            await _emit(event_callback, "step.started", {"step": step})
            request = ChatRequest(messages=messages, tools=tools or None)

            try:
                response = await self.provider.chat(request)
            except WolfError:
                raise
            except Exception as exc:
                logger.exception("agent_loop_model_call_failed", loop_id=loop_id)
                await self._audit_model_failure(db, ctx, loop_id, step, str(exc))
                await _emit(event_callback, "model.call.failed", {
                    "step": step, "detail": str(exc),
                })
                answer = AgentAnswer(
                    content=f"Model call failed: {exc}",
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
            await _emit(event_callback, "model.call.completed", {
                "step": step,
                "input_tokens": response.input_tokens,
                "output_tokens": response.output_tokens,
                "stop_reason": response.stop_reason,
                "tool_call_count": len(response.tool_calls),
            })

            messages.append(
                Message(
                    role=MessageRole.assistant,
                    content=response.content,
                    tool_calls=response.tool_calls or None,
                )
            )

            if not response.tool_calls:
                logger.info(
                    "agent_loop_completed",
                    loop_id=loop_id,
                    stop_reason="answer",
                    steps=step + 1,
                    tool_calls=tool_call_count,
                )
                answer = AgentAnswer(
                    content=response.content,
                    citations=citations,
                    step_count=step + 1,
                    tool_call_count=tool_call_count,
                    input_tokens=total_input_tokens,
                    output_tokens=total_output_tokens,
                    stop_reason="answer",
                    loop_id=loop_id,
                )
                await _emit(event_callback, "answer", answer.model_dump(mode="json"))
                return answer

            tool_results: list[ToolResult] = []
            for call in response.tool_calls:
                tool_call_count += 1
                dispatch_result = await dispatch_tool_call(
                    call, ctx=ctx, db=db, opensearch=opensearch,
                    server_api=server_api, limits=self.limits,
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
                    tool_results.append(ToolResult(
                        tool_call_id=call.id, name=call.name,
                        content=dispatch_result.result,
                    ))
                else:
                    tool_results.append(ToolResult(
                        tool_call_id=call.id, name=call.name, content="",
                        error=dispatch_result.error or "tool call failed",
                    ))

            messages.append(Message(role=MessageRole.tool, tool_results=tool_results))

        # Budget exhausted without final answer.
        logger.warning(
            "agent_loop_budget_exhausted",
            loop_id=loop_id, steps=budget, tool_calls=tool_call_count,
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
        await _emit(event_callback, "answer", answer.model_dump(mode="json"))
        return answer

    # ── Audit helpers ─────────────────────────────────────────────────────

    async def _audit_model_success(
        self,
        db: AsyncSession,
        ctx: TenantContext,
        loop_id: str,
        step: int,
        response: ChatResponse,
    ) -> None:
        await write_event_from_context(
            db, ctx,
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
        ctx: TenantContext,
        loop_id: str,
        step: int,
        detail: str,
    ) -> None:
        await write_event_from_context(
            db, ctx,
            event_type="model.call.failure",
            event_data={
                "loop_id": loop_id,
                "step": step,
                "detail": detail[:1000],
                "provider": self.provider.capability().provider,
            },
        )
