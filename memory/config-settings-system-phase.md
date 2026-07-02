---
name: config-settings-system-phase
description: "Phase 6.10 (planned 2026-06-16; scope EXPANDED 2026-07-02): the Superuser config-settings SYSTEM implementing ADR 0019 — DB-source-of-truth runtime config synced file ⇄ CLI ⇄ Web GUI, Superuser-only, audited. EXPANDED: per-component config planes (server/database/dashboard, each its own central file) + Wolf config REACHES its tech stack (service-level Ollama knobs via a privileged wolf-tune helper — no manual sudo rituals). Consumers: same-network gate, model posture, grounding mode."
metadata:
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

PLAN (Phase 6.10, prompted 2026-06-16 when the same-network gate
[[same-network-gate-deferred]] needed a GUI toggle): build the runtime
config-settings system that **ADR 0019** (web-first-configurability) mandates
but Wolf doesn't have yet.

**Current state (the gap):** config is **env-only** — `wolf_server/config.py`
pydantic `Settings`. There is **no** DB settings table, **no** config API,
**no** config CLI, and the dashboard `/settings` area is only `access` + `users`
(no general Settings page). So "add a toggle in the Superuser Settings page,
synced with CLI + terminal" is not a button — it needs this substrate first.

**What 6.10 builds:**
- **DB as source of truth** for operator-settable knobs + a config API.
- A **Superuser Settings GUI page** (the dashboard surface).
- A **Wolf config CLI** (shell-wrapper pattern, [[shell-wrapper-required-pattern]]).
- All three surfaces — OS terminal/env ⇄ CLI ⇄ Web-GUI — stay **identical +
  synced** (DB is the truth; each is a view), every change **audited** (ADR 0019).
- **Authorized Superuser-only** per [[superuser-config-authority]].

**First consumer:** the **same-network gate** on/off toggle (the gate shipped
env-only + default-OFF in 6.5-h.2; this turns it into a synced Superuser
switch — the toggle's sole job is enable/disable the gate). Other env knobs
migrate in per ADR 0019's catalogue.

**Second concrete consumer (ADR 0024, 2026-06-18):** a **"Model posture"**
setting — *split* (`qwen3:4b` chat / `qwen3:8b` judge, the data-backed DEFAULT)
vs *unified* (`qwen3:8b` for both). Today these are the env knobs
`DEFAULT_MODEL_ID` + `GROUNDING_JUDGE_MODEL_ID`; 6.10 promotes them to a
Superuser GUI radio/toggle (same shape as the Wazuh single-vs-distributed
selector). ADR 0024 measured the trade LIVE on the 6 GB GPU: split is ~6 s
faster/grounded-turn + streams chat 3.4× faster (61.8 vs 18.0 tok/s); the
4b↔8b swap is only ~2–3 s warm (NOT the bottleneck); the judge leg (~22 s) is
posture-independent (8b either way) so the real grounding-latency levers are
judge-output length / evidence window / keep-warm. Unified-8b stays valid
(max answer-quality / idle-resilient) → hence a SELECTABLE setting, not a hard
default. Keep BOTH embedders (`nomic-embed-text` + `nomic-embed-text-v2-moe`,
ADR 0014 — neither self-sufficient). Revisit the default if a ≥10 GB GPU lets
4b+8b stay co-resident (then split has zero swap, strictly best).

**SCOPE EXPANSION (operator mandate, 2026-07-02)** — prompted by the KV-cache
sudo ritual (the q8_0 drop-in the operator had to apply by hand for the
[[ollama-num-ctx-tool-truncation]] speed recovery). Three additional requirements:

1. **Per-component config planes.** EVERY Wolf component gets its own central
   config file managing its respective tech stack: **wolf-server** (has `.env`),
   **wolf-dashboard** (has a thin `services/dashboard/.env.example` → becomes a
   first-class plane), **wolf-database** (today only indirect via wolf-server's
   `DATABASE_URL` + Postgres's own files → gets its own plane).
2. **Wolf config REACHES the tech stack it runs on** — "the config file must be
   directly connected to each component and technical stack." Two mechanism
   classes: (a) **per-request settings** (e.g. `OLLAMA_NUM_CTX`) — already work
   from Wolf's config, the model to generalize; (b) **service-level settings**
   (`OLLAMA_KV_CACHE_TYPE`, `OLLAMA_FLASH_ATTENTION`, `OLLAMA_NUM_PARALLEL` —
   root-owned systemd env, read at Ollama startup) — need a **privileged helper**
   (`wolf-tune`-style, the [[shell-wrapper-required-pattern]] like `wolf-cert`,
   narrowly-scoped sudoers entry) that Wolf's config layer invokes to write the
   systemd drop-in + restart the service. Users NEVER run sudo rituals by hand;
   they set a value in Wolf's config (file, CLI, or GUI) and Wolf applies it.
   GUI surfaces the honest caveat: service-level applies need an Ollama restart
   (~seconds of model reload). Ollama is the FIRST target; the principle covers
   every future stack component.
3. **Full three-way sync for EVERY plane** — direct file edit ⇄ supporting CLI
   ⇄ Web GUI must fully sync, mirror, and remain identical (the ADR 0019
   contract, extended from wolf-server's own knobs to all three components and
   their stacks).

**Adjacent follow-up:** **per-org trusted networks** (each org's own CIDRs) —
the MSSP-correct form of the same-network gate; open question whether
Superuser-set or org-admin-set.

ADR 0019 already governs the design; a focused implementation ADR can follow at
phase-open if the data model warrants. Related: [[web-first-configurability]],
[[superuser-config-authority]], [[same-network-gate-deferred]],
[[shell-wrapper-required-pattern]].
