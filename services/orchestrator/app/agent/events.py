"""Loop-event type emitted by the AgentLoop for streaming consumers.

The loop optionally takes an `event_callback` that is invoked at every
significant transition.  The SSE chat endpoint relays these events to the
browser; non-streaming callers ignore them.
"""

from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import BaseModel

LoopEventType = Literal[
    "loop.started",
    "step.started",
    "model.call.completed",
    "model.call.failed",
    "tool.call.completed",
    "answer",
]


class LoopEvent(BaseModel):
    """One observable transition inside the agent loop."""

    type: LoopEventType
    data: dict[str, Any]


EventCallback = Callable[[LoopEvent], Awaitable[None]]
