"""Agent strategies — frontier / guided / pipeline.

The strategy is picked from the model's capability descriptor.  All three
produce grounded, gated output; they differ in how much they lean on the
model versus deterministic scaffolding.
"""

from abc import ABC, abstractmethod

from wolf_schema import ToolSchema
from wolf_schema.capability import AgentStrategy, CapabilityDescriptor

from wolf_server.agent.prompts import GUIDED_SUFFIX, PIPELINE_SUFFIX, SYSTEM_PROMPT


class Strategy(ABC):
    """A driver that decides step budget, prompt, and tool surface for the loop."""

    name: str

    @abstractmethod
    def step_budget(self, capability: CapabilityDescriptor) -> int: ...

    @abstractmethod
    def system_prompt(self) -> str: ...

    @abstractmethod
    def model_tools(self, all_tools: list[ToolSchema]) -> list[ToolSchema]: ...


class FrontierStrategy(Strategy):
    """Full autonomy — the model plans and acts within a generous step budget."""

    name = "frontier"

    def step_budget(self, capability: CapabilityDescriptor) -> int:
        return capability.max_safe_autonomous_steps

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT

    def model_tools(self, all_tools: list[ToolSchema]) -> list[ToolSchema]:
        return all_tools


class GuidedStrategy(Strategy):
    """Shorter budget with explicit sub-task narration in the prompt."""

    name = "guided"

    def step_budget(self, capability: CapabilityDescriptor) -> int:
        return min(capability.max_safe_autonomous_steps, 8)

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT + GUIDED_SUFFIX

    def model_tools(self, all_tools: list[ToolSchema]) -> list[ToolSchema]:
        # In future iterations this can narrow per sub-task; for Phase 2B
        # the model sees the full read catalog.
        return all_tools


class PipelineStrategy(Strategy):
    """Deterministic outer scaffolding; the model only summarizes.

    Phase 2B baseline: the model receives no tools and answers from the
    context wolf-server has assembled.  A future iteration will
    classify the question shape and pre-fetch via dispatch before invoking
    the model — the "model fills slots" pattern from doc 02.
    """

    name = "pipeline"

    def step_budget(self, capability: CapabilityDescriptor) -> int:
        # One model call; the pipeline does all the orchestration.
        return 1

    def system_prompt(self) -> str:
        return SYSTEM_PROMPT + PIPELINE_SUFFIX

    def model_tools(self, _all_tools: list[ToolSchema]) -> list[ToolSchema]:
        return []


def strategy_for(capability: CapabilityDescriptor) -> Strategy:
    """Map a capability descriptor to the matching Strategy instance."""
    match capability.recommended_strategy:
        case AgentStrategy.frontier:
            return FrontierStrategy()
        case AgentStrategy.guided:
            return GuidedStrategy()
        case AgentStrategy.pipeline:
            return PipelineStrategy()
