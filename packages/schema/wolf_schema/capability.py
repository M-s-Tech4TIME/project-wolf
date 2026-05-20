"""Model capability descriptor and grading enums."""

from enum import StrEnum

from pydantic import BaseModel


class ReasoningTier(StrEnum):
    frontier = "frontier"
    strong = "strong"
    mid = "mid"
    basic = "basic"


class NativeToolCalling(StrEnum):
    full = "full"
    partial = "partial"
    none = "none"


class StructuredOutput(StrEnum):
    schema_enforced = "schema_enforced"
    prompt_coaxed = "prompt_coaxed"
    unreliable = "unreliable"


class AgentStrategy(StrEnum):
    frontier = "frontier"  # autonomous multi-step
    guided = "guided"      # checkpoints per sub-task
    pipeline = "pipeline"  # deterministic, model fills slots


class CapabilityDescriptor(BaseModel):
    """Capability profile for a configured model instance."""

    model_id: str
    provider: str
    context_window: int
    native_tool_calling: NativeToolCalling
    reasoning_tier: ReasoningTier
    structured_output: StructuredOutput
    max_safe_autonomous_steps: int
    recommended_strategy: AgentStrategy
