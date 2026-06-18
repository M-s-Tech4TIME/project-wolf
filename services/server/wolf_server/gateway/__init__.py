"""In-process approval gateway — Phase 6 (ADR 0025).

Wolf is capability-driven: it may act within whatever the per-org Wazuh
credential's RBAC authorizes, but every state-changing action is
**proposed → human-approved → executed → verified → audited**.  This package
is the in-process implementation of doc 04's approval gateway (the separate
`services/gateway/` service stays a reserved stub per ADR 0025 decision A2):

  - :mod:`models`        — the ``ActionProposal`` row + ``ProposalState`` enum
  - :mod:`state_machine` — the forward-only, gated lifecycle
  - :mod:`proposals`     — create + freeze (content hash) a proposal
  - :mod:`approval`      — approve/reject (separation of duties + capability)
  - :mod:`execution`     — freshness re-check → bounded write → verification read
  - :mod:`validator`     — the pre-queue action validator (hard gate)
"""
