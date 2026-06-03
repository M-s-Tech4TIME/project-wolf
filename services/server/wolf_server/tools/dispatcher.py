"""Tool dispatcher — the chokepoint every model-originated tool call passes through.

Flow (matches doc 03 §The dispatch flow):

    on model_tool_call(call):
        schema = schema_registry.validate_model_call(call.name)
                # rejects unknown tools and execute-tier tools, audits anomaly
        runner = runtime_registry.get(call.name)
        args = runner.InputModel(**sanitize(call.arguments))
                # rejects model-supplied tenant_id; validates argument schema
        await rate_limiter.take(ctx.tenant_id)
                # enforce per-tenant rate limit
        result = await runner.run(exec_ctx, args)
        runner.OutputModel.model_validate(result.model_dump())
                # validate output schema before returning to the model
        await audit.write_event(tool.call.success, ...)
        return result

Every branch — success, schema rejection, capability rejection, guardrail
rejection, runtime failure — writes an audit event.  Storage is cheap;
forensic gaps are not.
"""

import time
from typing import Any

import structlog
from pydantic import BaseModel, ValidationError
from sqlalchemy.ext.asyncio import AsyncSession
from wolf_common.errors import (
    ToolCapabilityError,
    ToolNotFoundError,
    WolfError,
)
from wolf_schema import ToolCall

from wolf_server.audit.log import write_event_from_context
from wolf_server.guardrails.limits import GuardrailViolation, ResourceLimits
from wolf_server.guardrails.rate_limit import TenantRateLimiter, default_rate_limiter
from wolf_server.models.registry import registry as schema_registry
from wolf_server.tenancy.context import TenantContext
from wolf_server.tools.base import (
    ToolExecContext,
    sanitize_tenant_id_from_args,
    strip_explicit_nulls,
)
from wolf_server.tools.registry import runtime_registry
from wolf_server.wazuh.opensearch import WazuhOpenSearchClient
from wolf_server.wazuh.server_api import WazuhServerApiClient

logger = structlog.get_logger(__name__)


class ToolDispatchResult(BaseModel):
    """What the dispatcher returns to the agent loop."""

    tool_call_id: str
    tool_name: str
    success: bool
    result: dict[str, Any] | None = None
    error: str | None = None
    elapsed_ms: int


async def dispatch_tool_call(
    call: ToolCall,
    *,
    ctx: TenantContext,
    db: AsyncSession,
    opensearch: WazuhOpenSearchClient,
    server_api: WazuhServerApiClient,
    limits: ResourceLimits,
    rate_limiter: TenantRateLimiter = default_rate_limiter,
    knowledge_store: Any | None = None,
    cache: Any | None = None,
) -> ToolDispatchResult:
    """Validate, guardrail, audit, run, and validate-out a single tool call.

    Never raises for control-flow failures (unknown tool, schema invalid,
    guardrail violation, runtime error) — they are returned as a
    ToolDispatchResult with `success=False` and an audited error.  The
    agent loop can decide how to surface this to the model (typically: feed
    the error back so the model can correct).

    Re-raises only for catastrophic states (a TenantMismatchError from the
    OpenSearch client, e.g.) — these are security incidents that must bubble.
    """
    start = time.perf_counter()
    sanitized_args = strip_explicit_nulls(
        sanitize_tenant_id_from_args(call.arguments, ctx.tenant_id)
    )

    # 1. Schema-registry validation: tool exists and is not execute-tier.
    try:
        tool_schema = schema_registry.validate_model_call(call.name)
    except ToolNotFoundError as exc:
        return await _audit_failure(
            db, ctx, call, "tool.call.unknown", str(exc), start
        )
    except ToolCapabilityError as exc:
        # Model named an execute-tier tool — a structural anomaly.  Always log loud.
        logger.error(
            "model_attempted_execute_tool",
            tool_name=call.name,
            tenant_id=str(ctx.tenant_id),
        )
        return await _audit_failure(
            db, ctx, call, "tool.call.anomaly", str(exc), start
        )

    # 2. Runtime lookup.
    try:
        runner = runtime_registry.get(call.name)
    except ToolNotFoundError as exc:
        # Schema registered without a runner — programming error.
        logger.error("tool_runner_missing", tool_name=call.name)
        return await _audit_failure(
            db, ctx, call, "tool.call.runner_missing", str(exc), start
        )

    # 3. Input-schema validation.
    try:
        args_model = runner.InputModel(**sanitized_args)
    except ValidationError as exc:
        return await _audit_failure(
            db,
            ctx,
            call,
            "tool.call.schema_invalid",
            f"Input validation failed: {exc.errors()}",
            start,
        )

    # 4. Per-tenant rate limit.
    try:
        await rate_limiter.take(ctx.tenant_id)
    except GuardrailViolation as exc:
        return await _audit_failure(
            db, ctx, call, "tool.call.rate_limited", str(exc), start
        )

    # 5. Execute.
    exec_ctx = ToolExecContext(
        tenant=ctx,
        limits=limits,
        opensearch=opensearch,
        server_api=server_api,
        knowledge_store=knowledge_store,
        cache=cache,
    )
    try:
        result = await runner.run(exec_ctx, args_model)
    except GuardrailViolation as exc:
        return await _audit_failure(
            db, ctx, call, "tool.call.guardrail", str(exc), start
        )
    except WolfError:
        # TenantMismatchError or other security-relevant Wolf errors — bubble.
        raise
    except Exception as exc:
        # Unexpected runtime failure — audit and surface as a clean tool error.
        logger.exception("tool_runtime_error", tool_name=call.name)
        return await _audit_failure(
            db, ctx, call, "tool.call.runtime_error", str(exc), start
        )

    # 6. Output-schema validation.
    try:
        runner.OutputModel.model_validate(result.model_dump())
    except ValidationError as exc:
        # The TOOL produced invalid output — that is our bug.  Audit and fail.
        logger.error("tool_output_invalid", tool_name=call.name, errors=exc.errors())
        return await _audit_failure(
            db,
            ctx,
            call,
            "tool.call.output_invalid",
            f"Output validation failed: {exc.errors()}",
            start,
        )

    elapsed_ms = int((time.perf_counter() - start) * 1000)
    result_dict = result.model_dump(mode="json")

    await write_event_from_context(
        db,
        ctx,
        event_type="tool.call.success",
        event_data={
            "tool_name": call.name,
            "tool_call_id": call.id,
            "arguments_summary": _summarize_args(sanitized_args),
            "result_summary": _summarize_result(result_dict),
            "elapsed_ms": elapsed_ms,
            "tier": tool_schema.tier.value,
        },
    )

    return ToolDispatchResult(
        tool_call_id=call.id,
        tool_name=call.name,
        success=True,
        result=result_dict,
        elapsed_ms=elapsed_ms,
    )


# ── Helpers ───────────────────────────────────────────────────────────────────


async def _audit_failure(
    db: AsyncSession,
    ctx: TenantContext,
    call: ToolCall,
    event_type: str,
    detail: str,
    start: float,
) -> ToolDispatchResult:
    elapsed_ms = int((time.perf_counter() - start) * 1000)
    await write_event_from_context(
        db,
        ctx,
        event_type=event_type,
        event_data={
            "tool_name": call.name,
            "tool_call_id": call.id,
            "detail": detail[:1000],
            "elapsed_ms": elapsed_ms,
        },
    )
    return ToolDispatchResult(
        tool_call_id=call.id,
        tool_name=call.name,
        success=False,
        error=detail,
        elapsed_ms=elapsed_ms,
    )


def _summarize_args(args: dict[str, Any]) -> dict[str, Any]:
    """Sanitize argument dict for the audit log — no raw payloads.

    Keys with potential PII or large content are summarized to their length.
    """
    summary: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and len(value) > 200:
            summary[key] = f"<str, {len(value)} chars>"
        else:
            summary[key] = value
    return summary


def _summarize_result(result: dict[str, Any]) -> dict[str, Any]:
    """Trim result for the audit log — keep counts and citations, drop bulk."""
    summary: dict[str, Any] = {}
    for key, value in result.items():
        if isinstance(value, list):
            summary[f"{key}_count"] = len(value)
        elif isinstance(value, dict):
            summary[f"{key}_keys"] = list(value.keys())
        else:
            summary[key] = value
    return summary
