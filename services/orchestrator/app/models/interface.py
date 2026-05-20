"""ModelProvider protocol, known-model defaults, and provider factory."""

from collections.abc import AsyncIterator
from typing import Protocol, runtime_checkable

from wolf_schema import CapabilityDescriptor, ChatRequest, ChatResponse
from wolf_schema.capability import (
    AgentStrategy,
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
    ),
    # Ollama / local models ──────────────────────────────────────────────────
    # Capability here is for the base model family; actual performance varies
    # by quantisation.  Run the model probe to get empirical measurements.
    "llama3.2": CapabilityDescriptor(
        model_id="llama3.2",
        provider="ollama",
        context_window=128_000,
        native_tool_calling=NativeToolCalling.partial,
        reasoning_tier=ReasoningTier.mid,
        structured_output=StructuredOutput.prompt_coaxed,
        max_safe_autonomous_steps=8,
        recommended_strategy=AgentStrategy.guided,
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
