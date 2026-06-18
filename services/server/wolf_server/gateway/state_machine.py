"""Proposal state machine — forward-only, gated transitions (doc 04).

The lifecycle::

     draft ──▶ pending ──┬──▶ approved ──▶ executing ──┬──▶ succeeded ──▶ rolled_back
                         │                             │
                         ├──▶ rejected                 └──▶ failed
                         │
                         └──▶ expired

Transitions are one-directional and gated; an illegal transition is a
programming error and raises :class:`IllegalTransition`.  Each *legal*
transition is, at the call site, paired with an append-only audit event.
"""

from wolf_common.errors import WolfError

from wolf_server.gateway.models import ProposalState

# from-state -> the set of states it may move to.
ALLOWED_TRANSITIONS: dict[ProposalState, frozenset[ProposalState]] = {
    ProposalState.draft: frozenset({ProposalState.pending}),
    ProposalState.pending: frozenset(
        {ProposalState.approved, ProposalState.rejected, ProposalState.expired}
    ),
    ProposalState.approved: frozenset({ProposalState.executing, ProposalState.expired}),
    ProposalState.executing: frozenset({ProposalState.succeeded, ProposalState.failed}),
    ProposalState.succeeded: frozenset({ProposalState.rolled_back}),
    ProposalState.failed: frozenset(),
    ProposalState.rejected: frozenset(),
    ProposalState.expired: frozenset(),
    ProposalState.rolled_back: frozenset(),
}


class IllegalTransitionError(WolfError):
    """A proposal was asked to move along an edge the state machine forbids."""

    http_status = 409
    error_code = "proposal_illegal_transition"


def can_transition(current: ProposalState, target: ProposalState) -> bool:
    return target in ALLOWED_TRANSITIONS.get(current, frozenset())


def assert_transition(current: ProposalState, target: ProposalState) -> None:
    """Raise :class:`IllegalTransitionError` unless ``current → target`` is allowed."""
    if not can_transition(current, target):
        raise IllegalTransitionError(
            f"Proposal cannot move {current.value!r} → {target.value!r}"
        )
