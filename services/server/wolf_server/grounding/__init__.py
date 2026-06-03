"""Phase 3 Slice 2B — grounding validator.

Per `docs/06-knowledge-and-rag.md` §Hallucinated grounding: the agent's
draft answer is checked against the citation evidence (tool results +
retrieved knowledge chunks). Claims that can't be traced back are
surfaced to the user as `[unverified]`.

This is the structural mitigation for the cross-model
grounding-discipline failures recorded in ADRs 0002 / 0010 / 0011 and
the synthesis-embellishment observed during Slice 1's mixed-mode test.
"""

from wolf_server.grounding.validator import (
    ClaimVerdict,
    GroundingValidator,
    ValidationResult,
)

__all__ = ["ClaimVerdict", "GroundingValidator", "ValidationResult"]
