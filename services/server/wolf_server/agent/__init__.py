"""Agent loop, strategies, and model resolver.

The agent loop is the top-level interface to wolf-server's reasoning
capability.  It drives a ModelProvider in a plan-act-observe loop, with the
strategy chosen by the model's capability descriptor.
"""

from wolf_server.agent.loop import AgentAnswer, AgentLoop
from wolf_server.agent.model_resolver import (
    ModelProviderUnconfiguredError,
    get_grounding_judge_model,
    get_model_for_organization,
)
from wolf_server.agent.strategies import (
    FrontierStrategy,
    GuidedStrategy,
    PipelineStrategy,
    Strategy,
    strategy_for,
)

__all__ = [
    "AgentAnswer",
    "AgentLoop",
    "FrontierStrategy",
    "GuidedStrategy",
    "ModelProviderUnconfiguredError",
    "PipelineStrategy",
    "Strategy",
    "get_grounding_judge_model",
    "get_model_for_organization",
    "strategy_for",
]
