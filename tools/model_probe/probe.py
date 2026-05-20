"""Model probe — runs the battery of tasks and grades the model.

Usage (from the repo root inside the uv venv):
    uv run python -m tools.model_probe --provider ollama --model llama3.2
    uv run python -m tools.model_probe --provider anthropic --model claude-sonnet-4-6
    uv run python -m tools.model_probe --provider openai --model gpt-4o
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.models.interface import ModelProvider

from wolf_schema.capability import (
    AgentStrategy,
    CapabilityDescriptor,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)

from tools.model_probe.tasks import (
    ALL_TASKS,
    ProbeTaskResult,
    _infer_tool_calling,
)


@dataclass
class ProbeReport:
    model_id: str
    provider: str
    measured_capability: CapabilityDescriptor
    task_results: list[ProbeTaskResult]
    probe_timestamp: datetime = field(
        default_factory=lambda: datetime.now(tz=UTC)
    )

    @property
    def overall_score(self) -> float:
        if not self.task_results:
            return 0.0
        return sum(r.score for r in self.task_results) / len(self.task_results)

    def summary(self) -> str:
        lines = [
            f"Model:     {self.model_id}  ({self.provider})",
            f"Timestamp: {self.probe_timestamp.isoformat()}",
            f"Score:     {self.overall_score:.2f}  "
            f"({len([r for r in self.task_results if r.passed])}"
            f"/{len(self.task_results)} tasks passed)",
            "",
            "Task results:",
        ]
        for r in self.task_results:
            status = "PASS" if r.passed else "FAIL"
            lines.append(f"  [{status}] {r.task_name:<30} score={r.score:.2f}  {r.notes}")
        lines.append("")
        lines.append("Measured capability:")
        cap = self.measured_capability
        lines.append(f"  reasoning_tier:          {cap.reasoning_tier.value}")
        lines.append(f"  native_tool_calling:     {cap.native_tool_calling.value}")
        lines.append(f"  structured_output:       {cap.structured_output.value}")
        lines.append(f"  max_safe_auto_steps:     {cap.max_safe_autonomous_steps}")
        lines.append(f"  recommended_strategy:    {cap.recommended_strategy.value}")
        return "\n".join(lines)


def _grade(
    results: list[ProbeTaskResult],
    declared: CapabilityDescriptor,
) -> CapabilityDescriptor:
    """Derive a measured CapabilityDescriptor from probe task results."""
    overall = sum(r.score for r in results) / len(results) if results else 0.0

    tool_calling = _infer_tool_calling(results)

    json_task = next(r for r in results if r.task_name == "json_schema_adherence")
    if json_task.passed:
        structured = StructuredOutput.schema_enforced
    elif json_task.score >= 0.5:
        structured = StructuredOutput.prompt_coaxed
    else:
        structured = StructuredOutput.unreliable

    multi = next(r for r in results if r.task_name == "multi_step_reasoning")
    grounding = next(r for r in results if r.task_name == "grounding_discipline")

    if overall >= 0.85 and multi.passed and grounding.passed:
        tier = ReasoningTier.frontier
        max_steps = 15
        strategy = AgentStrategy.frontier
    elif overall >= 0.65 and multi.passed:
        tier = ReasoningTier.mid
        max_steps = 8
        strategy = AgentStrategy.guided
    elif overall >= 0.4:
        tier = ReasoningTier.basic
        max_steps = 4
        strategy = AgentStrategy.pipeline
    else:
        tier = ReasoningTier.basic
        max_steps = 3
        strategy = AgentStrategy.pipeline

    # Downgrade tool-calling to fallback path for partial/none
    if tool_calling == NativeToolCalling.none:
        max_steps = min(max_steps, 4)
        strategy = AgentStrategy.pipeline

    return CapabilityDescriptor(
        model_id=declared.model_id,
        provider=declared.provider,
        context_window=declared.context_window,
        native_tool_calling=tool_calling,
        reasoning_tier=tier,
        structured_output=structured,
        max_safe_autonomous_steps=max_steps,
        recommended_strategy=strategy,
    )


async def run_probe(provider: ModelProvider) -> ProbeReport:
    """Run the full task battery and return a ProbeReport."""
    declared = provider.capability()
    results: list[ProbeTaskResult] = []
    for task in ALL_TASKS:
        result = await task(provider)
        results.append(result)

    measured = _grade(results, declared)
    return ProbeReport(
        model_id=declared.model_id,
        provider=declared.provider,
        measured_capability=measured,
        task_results=results,
    )


def run_probe_sync(provider: ModelProvider) -> ProbeReport:
    """Synchronous wrapper for use in CLI and tests."""
    return asyncio.run(run_probe(provider))
