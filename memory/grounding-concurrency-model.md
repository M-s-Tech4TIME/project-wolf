---
name: grounding-concurrency-model
description: "STANDING PRINCIPLE (2026-07-01, ADR 0026 addendum): grounding is per-request PARALLEL/concurrent for every org/user/thread/message — like Claude serving millions at once. NEVER serialize/queue grounding (rejected before any code). App layer = unbounded; only OLLAMA_NUM_PARALLEL + VRAM (infra) governs simultaneous execution."
metadata:
  type: project
---

**Grounding runs fully parallel + concurrent for every organisation, user, chat
thread, and message — no response's verdict chips ever wait behind another's.**
The bar is Claude: millions of users served simultaneously, each with a dedicated
experience, nobody waiting on anybody. This is FOUNDATIONAL to Wolf's MSSP goal
(many users across many orgs interacting at once).

**Why:** MSSP means a shared judge model must never become a single-lane
bottleneck. Serialising grounding (making analyst B wait for analyst A's — or
their own previous message's — verdicts) is antithetical to the whole project.

**How to apply:**
- This is ALREADY the architecture (verified 2026-07-01, not aspirational): each
  chat message is an independent `POST /chat/stream` request in its OWN asyncio
  task with its OWN judge provider + `GroundingValidator` + DB session
  (`api/chat.py`); grounding runs inside that per-request task. There is **NO
  lock / semaphore / queue anywhere on the grounding path** — keep it that way.
- **Never add grounding serialization.** A FIFO grounding queue (global, then
  per-conversation) was proposed on 2026-07-01 and **REJECTED before any code was
  written** — the operator reversed it himself on MSSP grounds. Do not re-propose.
- The app layer has NO concurrency ceiling. The ONLY governor of how many
  groundings execute at the same instant is **infrastructure**: Ollama's
  `OLLAMA_NUM_PARALLEL` (>1 = continuous batching; UNSET on the dev GPU by choice
  — raising it there thrashes VRAM since `qwen3:8b` spills to CPU) + VRAM, or
  horizontal model-server replicas / a hosted endpoint. The dev-GPU limit is a
  HARDWARE constraint, never a design constraint.
- **Wolf is built for enterprise/on-prem on the operator's own, more capable
  hardware.** There the deployer unlocks true simultaneous grounding purely by
  provisioning capacity (raise `OLLAMA_NUM_PARALLEL` + VRAM, or add replicas) —
  **no Wolf code change**. Concurrency scales with the iron. Keeps
  [[single-org-mssp-parity]]: the same code serving one analyst serves thousands
  in parallel.

See ADR 0026 addendum (2026-07-01) + [[grounding-execution-modes]]
(blocking/deferred/incremental — `incremental` becomes real wall-clock
concurrency at `OLLAMA_NUM_PARALLEL>=2`).
