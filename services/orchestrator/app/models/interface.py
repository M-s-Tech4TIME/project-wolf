"""ModelProvider protocol, known-model defaults, and provider factory."""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from wolf_schema import CapabilityDescriptor, ChatRequest, ChatResponse
from wolf_schema.capability import (
    AgentStrategy,
    LicenseClass,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)


@runtime_checkable
class ModelProvider(Protocol):
    """Adapter contract all LLM provider implementations must satisfy."""

    def capability(self) -> CapabilityDescriptor:
        """Return the static capability descriptor for this model."""
        ...

    async def chat(self, request: ChatRequest) -> ChatResponse:
        """Send a chat completion request and return the full response."""
        ...

    def stream(self, request: ChatRequest) -> AsyncIterator[str]:
        """Stream response tokens.  Yields the full content as one chunk in
        providers that do not yet implement real streaming."""
        ...


# ── Known-model defaults ────────────────────────────────────────────────────
# Operators may override any field via tenant model config.
# Add new entries here as models are empirically graded.

KNOWN_MODELS: dict[str, CapabilityDescriptor] = {
    # Anthropic ──────────────────────────────────────────────────────────────
    "claude-opus-4-7": CapabilityDescriptor(
        model_id="claude-opus-4-7",
        provider="anthropic",
        context_window=200_000,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.frontier,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=20,
        recommended_strategy=AgentStrategy.frontier,
        license_class=LicenseClass.proprietary,
    ),
    "claude-sonnet-4-6": CapabilityDescriptor(
        model_id="claude-sonnet-4-6",
        provider="anthropic",
        context_window=200_000,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.frontier,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=15,
        recommended_strategy=AgentStrategy.frontier,
        license_class=LicenseClass.proprietary,
    ),
    "claude-haiku-4-5": CapabilityDescriptor(
        model_id="claude-haiku-4-5",
        provider="anthropic",
        context_window=200_000,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.mid,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=10,
        recommended_strategy=AgentStrategy.guided,
        license_class=LicenseClass.proprietary,
    ),
    # OpenAI ─────────────────────────────────────────────────────────────────
    "gpt-4o": CapabilityDescriptor(
        model_id="gpt-4o",
        provider="openai",
        context_window=128_000,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.frontier,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=15,
        recommended_strategy=AgentStrategy.frontier,
        license_class=LicenseClass.proprietary,
    ),
    "gpt-4o-mini": CapabilityDescriptor(
        model_id="gpt-4o-mini",
        provider="openai",
        context_window=128_000,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.mid,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=10,
        recommended_strategy=AgentStrategy.guided,
        license_class=LicenseClass.proprietary,
    ),
    # Ollama / local models ──────────────────────────────────────────────────
    # Capability here is for the base model family; actual performance varies
    # by quantisation.  Run the model probe to get empirical measurements.
    #
    # Llama family: Llama Community License (700M MAU cap, naming
    # requirements) — kept for backward compatibility and dev convenience
    # but flagged `restricted` per docs/14.  Not Wolf's recommended default.
    #
    # Two fields below were amended to match the LIVE PROBE measurement
    # (docs/decisions/0001-model-probe-llama3.2-baseline.md, 2026-05-22):
    # native_tool_calling was estimated `partial` and measured `full`;
    # structured_output was estimated `prompt_coaxed` and measured
    # `unreliable` (the model emits a syntactically correct tool call but
    # cannot reliably hold a free-form JSON document under schema
    # constraint — JSON test failed at column 25 of line 28).  Strategy
    # tier (`mid` / `guided`) was correct.
    "llama3.2": CapabilityDescriptor(
        model_id="llama3.2",
        provider="ollama",
        context_window=128_000,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.mid,
        structured_output=StructuredOutput.unreliable,
        max_safe_autonomous_steps=8,
        recommended_strategy=AgentStrategy.guided,
        license_class=LicenseClass.restricted,
    ),
    "llama3.2:1b": CapabilityDescriptor(
        model_id="llama3.2:1b",
        provider="ollama",
        context_window=128_000,
        native_tool_calling=NativeToolCalling.none,
        reasoning_tier=ReasoningTier.basic,
        structured_output=StructuredOutput.unreliable,
        max_safe_autonomous_steps=3,
        recommended_strategy=AgentStrategy.pipeline,
        license_class=LicenseClass.restricted,
    ),
    "llama3.1:8b": CapabilityDescriptor(
        model_id="llama3.1:8b",
        provider="ollama",
        context_window=128_000,
        native_tool_calling=NativeToolCalling.partial,
        reasoning_tier=ReasoningTier.mid,
        structured_output=StructuredOutput.prompt_coaxed,
        max_safe_autonomous_steps=8,
        recommended_strategy=AgentStrategy.guided,
        license_class=LicenseClass.restricted,
    ),
    "mistral:7b": CapabilityDescriptor(
        model_id="mistral:7b",
        provider="ollama",
        context_window=32_768,
        native_tool_calling=NativeToolCalling.none,
        reasoning_tier=ReasoningTier.basic,
        structured_output=StructuredOutput.prompt_coaxed,
        max_safe_autonomous_steps=4,
        recommended_strategy=AgentStrategy.pipeline,
        license_class=LicenseClass.apache_2_0,
    ),
    "qwen2.5:7b": CapabilityDescriptor(
        model_id="qwen2.5:7b",
        provider="ollama",
        context_window=128_000,
        native_tool_calling=NativeToolCalling.partial,
        reasoning_tier=ReasoningTier.mid,
        structured_output=StructuredOutput.prompt_coaxed,
        max_safe_autonomous_steps=7,
        recommended_strategy=AgentStrategy.guided,
        license_class=LicenseClass.apache_2_0,
    ),
    # ─── New (docs/14) Apache/MIT-licensed candidates ─────────────────────
    # These are STATIC ESTIMATES per docs/14-model-recommendations.md and
    # the published documentation of each model.  Run the capability probe
    # to refine on specific hardware.
    #
    # Profile A — CPU-only, 16-32 GB RAM, no GPU.
    # Static fields below match the LIVE PROBE measurement
    # (docs/decisions/0002-model-probe-qwen3-4b.md, 2026-05-22):
    # the conservative initial estimate (basic / pipeline / partial /
    # prompt_coaxed) was upgraded across the board after the probe
    # measured mid / guided / full / schema_enforced on the dev VM.
    "qwen3:4b": CapabilityDescriptor(
        model_id="qwen3:4b",
        provider="ollama",
        context_window=131_072,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.mid,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=8,
        recommended_strategy=AgentStrategy.guided,
        license_class=LicenseClass.apache_2_0,
    ),
    # Static fields below match the LIVE PROBE measurement
    # (docs/decisions/0003-model-probe-gemma3-4b.md, 2026-05-22):
    # native_tool_calling downgraded `partial` → `none` because Gemma 3
    # 4B is trained without native tool-calling and Ollama returns HTTP
    # 400 on any chat request that includes a `tools` parameter;
    # structured_output upgraded `prompt_coaxed` → `schema_enforced`
    # because the JSON-adherence probe task passed cleanly; max steps
    # tightened 5 → 3.  Strategy tier (`basic` / `pipeline`) was correct.
    "gemma3:4b": CapabilityDescriptor(
        model_id="gemma3:4b",
        provider="ollama",
        context_window=131_072,
        native_tool_calling=NativeToolCalling.none,
        reasoning_tier=ReasoningTier.basic,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=3,
        recommended_strategy=AgentStrategy.pipeline,
        license_class=LicenseClass.apache_2_0,
    ),
    # Profile B — modest GPU (6-8 GB VRAM).  Expected first-real-deployment tier.
    # Brief asked for recommended_strategy="mid" — mapped to AgentStrategy.guided
    # (Wolf's mid-tier strategy is "guided agent with checkpoints").
    "qwen3:8b": CapabilityDescriptor(
        model_id="qwen3:8b",
        provider="ollama",
        context_window=131_072,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.mid,
        structured_output=StructuredOutput.prompt_coaxed,
        max_safe_autonomous_steps=10,
        recommended_strategy=AgentStrategy.guided,
        license_class=LicenseClass.apache_2_0,
    ),
    # Profile C / inference API — premium open agentic model.
    "glm-5.1": CapabilityDescriptor(
        model_id="glm-5.1",
        provider="ollama",
        context_window=128_000,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.frontier,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=20,
        recommended_strategy=AgentStrategy.frontier,
        license_class=LicenseClass.mit,
    ),
}


def default_descriptor_for(model_id: str, provider: str) -> CapabilityDescriptor:
    """Return the known-model default or a conservative unknown-model fallback."""
    if model_id in KNOWN_MODELS:
        return KNOWN_MODELS[model_id]
    return CapabilityDescriptor(
        model_id=model_id,
        provider=provider,
        context_window=8_192,
        native_tool_calling=NativeToolCalling.none,
        reasoning_tier=ReasoningTier.basic,
        structured_output=StructuredOutput.unreliable,
        max_safe_autonomous_steps=3,
        recommended_strategy=AgentStrategy.pipeline,
    )
