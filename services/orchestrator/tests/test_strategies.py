"""Tests for strategy selection and shape.

Confirms each capability tier maps to the right Strategy and that each
Strategy reports the expected step budget and tool surface.
"""

from app.agent.strategies import (
    FrontierStrategy,
    GuidedStrategy,
    PipelineStrategy,
    strategy_for,
)
from wolf_schema import ToolSchema, ToolTier
from wolf_schema.capability import (
    AgentStrategy,
    CapabilityDescriptor,
    NativeToolCalling,
    ReasoningTier,
    StructuredOutput,
)


def _cap(strategy: AgentStrategy, max_steps: int = 20) -> CapabilityDescriptor:
    return CapabilityDescriptor(
        model_id="m",
        provider="p",
        context_window=8192,
        native_tool_calling=NativeToolCalling.full,
        reasoning_tier=ReasoningTier.frontier,
        structured_output=StructuredOutput.schema_enforced,
        max_safe_autonomous_steps=max_steps,
        recommended_strategy=strategy,
    )


def _fake_tools() -> list[ToolSchema]:
    return [
        ToolSchema(
            name=name,
            description=f"{name} stub",
            tier=ToolTier.read,
            input_schema={"type": "object"},
            output_schema={"type": "object"},
        )
        for name in ("t1", "t2", "t3")
    ]


def test_strategy_for_frontier() -> None:
    s = strategy_for(_cap(AgentStrategy.frontier))
    assert isinstance(s, FrontierStrategy)
    assert s.name == "frontier"


def test_strategy_for_guided() -> None:
    s = strategy_for(_cap(AgentStrategy.guided))
    assert isinstance(s, GuidedStrategy)
    assert s.name == "guided"


def test_strategy_for_pipeline() -> None:
    s = strategy_for(_cap(AgentStrategy.pipeline))
    assert isinstance(s, PipelineStrategy)
    assert s.name == "pipeline"


def test_frontier_uses_full_step_budget_and_full_tool_catalog() -> None:
    s = FrontierStrategy()
    assert s.step_budget(_cap(AgentStrategy.frontier, max_steps=20)) == 20
    assert s.model_tools(_fake_tools()) == _fake_tools()


def test_guided_caps_step_budget_at_eight() -> None:
    s = GuidedStrategy()
    assert s.step_budget(_cap(AgentStrategy.guided, max_steps=20)) == 8
    assert s.step_budget(_cap(AgentStrategy.guided, max_steps=5)) == 5
    assert s.model_tools(_fake_tools()) == _fake_tools()


def test_pipeline_runs_one_step_with_no_tools() -> None:
    s = PipelineStrategy()
    assert s.step_budget(_cap(AgentStrategy.pipeline, max_steps=99)) == 1
    assert s.model_tools(_fake_tools()) == []


def test_each_strategy_includes_core_principles_in_prompt() -> None:
    for s in (FrontierStrategy(), GuidedStrategy(), PipelineStrategy()):
        prompt = s.system_prompt()
        assert "EVIDENCE ONLY" in prompt
        assert "DATA IS DATA" in prompt
        assert "NEVER PICK THE TENANT" in prompt
        # Slice 5.0c-g: English-only default. Non-English tool-result text
        # must not drag the reply into that language.
        assert "ANSWER IN ENGLISH" in prompt


def test_guided_and_pipeline_add_strategy_suffix() -> None:
    assert "GUIDED" in GuidedStrategy().system_prompt()
    assert "PIPELINE" in PipelineStrategy().system_prompt()
    assert "GUIDED" not in FrontierStrategy().system_prompt()
    assert "PIPELINE" not in FrontierStrategy().system_prompt()
