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


class LicenseClass(StrEnum):
    """Coarse license bucket per `docs/14-model-recommendations.md`.

    Informational only — no runtime code branches on this field.  Surfaces
    the open/restricted/proprietary distinction in operator-facing UIs so
    deployments aware of license posture can pick deliberately.
    """

    apache_2_0 = "apache-2.0"    # Qwen, Gemma, Mistral families
    mit = "mit"                  # GLM, DeepSeek, Kimi
    restricted = "restricted"    # Llama (community license w/ MAU cap)
    proprietary = "proprietary"  # Claude, GPT, Gemini, etc.


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
    # New in 2026-05-22: informational license classification.  Optional
    # for backward compatibility with descriptors built before this field
    # existed (test mocks, the model-probe baseline harness, etc.).
    license_class: LicenseClass | None = None
