"""Fixed battery of probe tasks used to grade a model's capability.

Each task returns a ProbeTaskResult with a pass/fail flag and a 0.0-1.0 score.
The grader in probe.py aggregates task scores into a CapabilityDescriptor.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from wolf_server.models.interface import ModelProvider

from wolf_schema import ChatRequest, ToolCall, ToolSchema
from wolf_schema.capability import NativeToolCalling
from wolf_schema.chat import Message, MessageRole
from wolf_schema.tools import ToolTier


@dataclass
class ProbeTaskResult:
    task_name: str
    passed: bool
    score: float  # 0.0 – 1.0
    notes: str


# ── Task 1: tool-call formatting ─────────────────────────────────────────────

_PING_TOOL = ToolSchema(
    name="ping",
    description="Returns the string 'pong'.  Use it to confirm tool-calling works.",
    tier=ToolTier.read,
    input_schema={"type": "object", "properties": {}, "required": []},
    output_schema={"type": "object", "properties": {"result": {"type": "string"}}},
)


async def task_tool_call_formatting(provider: ModelProvider) -> ProbeTaskResult:
    """Grade: can the model emit a valid, named tool call?"""
    request = ChatRequest(
        messages=[
            Message(
                role=MessageRole.user,
                content='Call the "ping" tool now.  Do not answer in prose.',
            )
        ],
        tools=[_PING_TOOL],
        max_tokens=256,
        temperature=0.0,
    )
    try:
        response = await provider.chat(request)
    except Exception as exc:  # noqa: BLE001
        return ProbeTaskResult(
            task_name="tool_call_formatting",
            passed=False,
            score=0.0,
            notes=f"chat() raised: {exc}",
        )

    if not response.tool_calls:
        return ProbeTaskResult(
            task_name="tool_call_formatting",
            passed=False,
            score=0.0,
            notes="Model returned no tool calls",
        )

    call: ToolCall = response.tool_calls[0]
    if call.name != "ping":
        return ProbeTaskResult(
            task_name="tool_call_formatting",
            passed=False,
            score=0.3,
            notes=f"Model called wrong tool: {call.name!r}",
        )

    return ProbeTaskResult(
        task_name="tool_call_formatting",
        passed=True,
        score=1.0,
        notes="Correct tool name emitted",
    )


# ── Task 2: JSON schema adherence ────────────────────────────────────────────

_ALERT_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "severity": {"type": "string", "enum": ["low", "medium", "high", "critical"]},
        "summary": {"type": "string"},
        "agent_id": {"type": "string"},
    },
    "required": ["severity", "summary", "agent_id"],
    "additionalProperties": False,
}


async def task_json_schema_adherence(provider: ModelProvider) -> ProbeTaskResult:
    """Grade: does the model produce JSON that validates against a schema?"""
    schema_str = json.dumps(_ALERT_SCHEMA, indent=2)
    request = ChatRequest(
        messages=[
            Message(
                role=MessageRole.user,
                content=(
                    "Produce a single JSON object that matches this schema exactly "
                    f"(no prose, no code fences):\n\n{schema_str}\n\n"
                    "Use agent_id=test-001, severity=high, and a short summary."
                ),
            )
        ],
        max_tokens=256,
        temperature=0.0,
    )
    try:
        response = await provider.chat(request)
    except Exception as exc:  # noqa: BLE001
        return ProbeTaskResult(
            task_name="json_schema_adherence",
            passed=False,
            score=0.0,
            notes=f"chat() raised: {exc}",
        )

    raw = response.content.strip()
    # Strip fences if the model disobeys
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        return ProbeTaskResult(
            task_name="json_schema_adherence",
            passed=False,
            score=0.0,
            notes=f"Response is not valid JSON: {exc}",
        )

    missing = [k for k in ("severity", "summary", "agent_id") if k not in data]
    if missing:
        return ProbeTaskResult(
            task_name="json_schema_adherence",
            passed=False,
            score=0.3,
            notes=f"Missing required keys: {missing}",
        )

    if data.get("severity") not in ("low", "medium", "high", "critical"):
        return ProbeTaskResult(
            task_name="json_schema_adherence",
            passed=False,
            score=0.5,
            notes=f"Invalid severity value: {data.get('severity')!r}",
        )

    return ProbeTaskResult(
        task_name="json_schema_adherence",
        passed=True,
        score=1.0,
        notes="JSON valid and schema-conformant",
    )


# ── Task 3: multi-step reasoning ─────────────────────────────────────────────

_LOOKUP_TOOL = ToolSchema(
    name="lookup_host",
    description="Given a hostname, return its IP address.",
    tier=ToolTier.read,
    input_schema={
        "type": "object",
        "properties": {"hostname": {"type": "string"}},
        "required": ["hostname"],
    },
    output_schema={
        "type": "object",
        "properties": {"ip": {"type": "string"}},
    },
)

_SCAN_TOOL = ToolSchema(
    name="scan_ip",
    description="Run a port scan against an IP address and return open ports.",
    tier=ToolTier.read,
    input_schema={
        "type": "object",
        "properties": {"ip": {"type": "string"}},
        "required": ["ip"],
    },
    output_schema={
        "type": "object",
        "properties": {"open_ports": {"type": "array", "items": {"type": "integer"}}},
    },
)


async def task_multi_step_reasoning(provider: ModelProvider) -> ProbeTaskResult:
    """Grade: does the model resolve a two-step dependency correctly?

    The model must call lookup_host before scan_ip because scan_ip requires the
    IP that lookup_host returns.  A model that jumps to scan_ip without first
    resolving the hostname fails this task.
    """
    request = ChatRequest(
        messages=[
            Message(
                role=MessageRole.user,
                content=(
                    "I need to scan the open ports on 'alpha.internal'.  "
                    "Use the available tools to find its IP first, then scan it."
                ),
            )
        ],
        tools=[_LOOKUP_TOOL, _SCAN_TOOL],
        max_tokens=512,
        temperature=0.0,
    )
    try:
        response = await provider.chat(request)
    except Exception as exc:  # noqa: BLE001
        return ProbeTaskResult(
            task_name="multi_step_reasoning",
            passed=False,
            score=0.0,
            notes=f"chat() raised: {exc}",
        )

    if not response.tool_calls:
        return ProbeTaskResult(
            task_name="multi_step_reasoning",
            passed=False,
            score=0.0,
            notes="Model returned no tool calls",
        )

    first_call = response.tool_calls[0]
    if first_call.name == "lookup_host":
        return ProbeTaskResult(
            task_name="multi_step_reasoning",
            passed=True,
            score=1.0,
            notes="Correctly called lookup_host first",
        )
    if first_call.name == "scan_ip":
        return ProbeTaskResult(
            task_name="multi_step_reasoning",
            passed=False,
            score=0.2,
            notes="Skipped lookup_host; called scan_ip without resolving hostname",
        )

    return ProbeTaskResult(
        task_name="multi_step_reasoning",
        passed=False,
        score=0.1,
        notes=f"Unexpected first tool call: {first_call.name!r}",
    )


# ── Task 4: grounding discipline ─────────────────────────────────────────────


async def task_grounding_discipline(provider: ModelProvider) -> ProbeTaskResult:
    """Grade: does the model refuse to fabricate facts when no data is given?

    A well-behaved model should say it needs to query data rather than inventing
    specific numbers, IPs, or events.
    """
    request = ChatRequest(
        messages=[
            Message(
                role=MessageRole.user,
                content=(
                    "Without using any tools, tell me exactly which IP address "
                    "made the most failed SSH login attempts on agent-007 in the "
                    "last 24 hours, and how many attempts it made."
                ),
            )
        ],
        max_tokens=256,
        temperature=0.0,
    )
    try:
        response = await provider.chat(request)
    except Exception as exc:  # noqa: BLE001
        return ProbeTaskResult(
            task_name="grounding_discipline",
            passed=False,
            score=0.0,
            notes=f"chat() raised: {exc}",
        )

    content = response.content.lower()
    refusal_signals = [
        "don't have access",
        "cannot access",
        "no access",
        "would need to",
        "would need access",
        "need to query",
        "need to check",
        "cannot tell",
        "unable to",
        "i don't have",
        "i do not have",
        "without querying",
        "without access",
        "no data",
        "no information",
    ]
    fabrication_signals = [
        # Patterns that suggest the model invented specific numbers
        "192.168.",
        "10.0.",
        "172.16.",
        " attempts",
        "failed login",
    ]

    refused = any(sig in content for sig in refusal_signals)
    fabricated = any(sig in content for sig in fabrication_signals)

    if refused and not fabricated:
        return ProbeTaskResult(
            task_name="grounding_discipline",
            passed=True,
            score=1.0,
            notes="Model correctly refused to fabricate specific data",
        )
    if fabricated:
        return ProbeTaskResult(
            task_name="grounding_discipline",
            passed=False,
            score=0.0,
            notes="Model appears to have fabricated specific data (IP or count)",
        )
    return ProbeTaskResult(
        task_name="grounding_discipline",
        passed=True,
        score=0.7,
        notes="Model did not fabricate but refusal was ambiguous",
    )


# ── Task registry ────────────────────────────────────────────────────────────

ALL_TASKS = [
    task_tool_call_formatting,
    task_json_schema_adherence,
    task_multi_step_reasoning,
    task_grounding_discipline,
]


def _infer_tool_calling(results: list[ProbeTaskResult]) -> NativeToolCalling:
    fmt = next(r for r in results if r.task_name == "tool_call_formatting")
    if fmt.passed:
        return NativeToolCalling.full
    if fmt.score > 0:
        return NativeToolCalling.partial
    return NativeToolCalling.none
