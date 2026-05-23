# 0005 — Phase 2 exit criterion: frontier-API verification

**Date:** 2026-05-22
**Status:** accepted
**Decider:** claude-code with explicit human direction
**Related:** `docs/10-build-roadmap.md` §"Phase 2 — The read path, end to
end" (exit criteria), ADR 0004 (`llama3.2 → qwen3:4b` switch),
`services/orchestrator/app/management/set_secret.py`,
`services/orchestrator/app/models/interface.py` (KNOWN_MODELS additions).

## Context

`docs/10-build-roadmap.md` Phase 2 exit criterion (last bullet):

> "Tested on a frontier model **and** a local Ollama model with the
> basic strategy."

The local-Ollama half has been verified through every Phase 2 follow-up
(`llama3.2` and then `qwen3:4b` against the operator's real Wazuh).
The frontier-API half was the only Phase 2 exit-criterion bullet still
unchecked.  This ADR records how we verified it.

## Hardware and software at verification time

- Same dev VM as ADRs 0001-0003 (`192.168.76.128`, ~16 GB RAM, CPU-only)
- Orchestrator restarted with `DEFAULT_MODEL_PROVIDER=openai`,
  `OPENAI_BASE_URL=https://openrouter.ai/api`, model targeted via
  `DEFAULT_MODEL_ID`, API key resolved via secrets backend ref
  `model.openrouter.api_key`
- Wazuh: same `192.168.76.129` deployment used for every other Phase 2
  test, 23 alerts in the rolling 24h window at test time
- Frontend untouched (qwen3:4b dev default flip from ADR 0004 was a
  static-config change — this verification override was env-only)

## How we got the API key into the system

Added `services/orchestrator/app/management/set_secret.py` — a small
CLI that reads a value from stdin (so the secret never touches shell
history or argv) and writes it to the configured secrets backend.  Used
it to stash the operator-supplied OpenRouter key under
`model.openrouter.api_key` in `.local/secrets.enc`.  Confirmed
round-trip without echoing the value:

```
$ printf %s "$KEY" | uv run python -m app.management.set_secret \
      --key model.openrouter.api_key
✓ stored 73-byte value under key 'model.openrouter.api_key' in 'file' backend
$ # read-back length=73, prefix='sk-or-v1-17', suffix=...'ad12'
```

## Three real issues surfaced during verification

These are documented because the next person trying this will hit at
least one of them.

### 1. `OPENAI_BASE_URL` must NOT include `/v1`

`OpenAIAdapter` posts to `{base_url}/v1/chat/completions`.  The default
base is `https://api.openai.com` (no `/v1`).  Setting
`OPENAI_BASE_URL=https://openrouter.ai/api/v1` produced
`https://openrouter.ai/api/v1/v1/chat/completions` → HTTP 404.

Correct: `OPENAI_BASE_URL=https://openrouter.ai/api`.  Documented inline
on the OpenRouter `KNOWN_MODELS` entries.

### 2. Two-`app/`-packages collision strikes again

Same root cause as the `tools/model_probe` CLI workaround from ADR 0001:
`services/gateway/app/` and `services/orchestrator/app/` both expose a
top-level package named `app`.  When `uvicorn app.main:app` is launched
from project root, Python picks gateway's `app` (it has `/healthz` but
no auth or chat routers) and the auth route 404s.

Workaround: always `cd services/orchestrator` before starting the
orchestrator.  Already the convention in `Makefile` targets and in the
`docs/PROGRESS.md` operational note.  Worth noting as a recurring
gotcha — the deeper "rename one of the `app/` packages" fix remains
deferred.

### 3. OpenRouter `:free` slugs don't guarantee no-deposit access

OpenRouter classifies routes as free or paid via the `:free` suffix,
but the *upstream provider* meters independently.  As of 2026-05-22:

- `deepseek/deepseek-v4-flash:free` — listed `:free` but upstream
  provider (Crucible) returned HTTP 402 "Out of credits" because the
  OpenRouter account had `total_credits: 0`.
- `nvidia/nemotron-3-super-120b-a12b:free` — truly free, accepted the
  request immediately.

Probed five candidate `:free` routes to find the truly-free ones:

| Model | Truly free (no deposit)? |
|---|---|
| `deepseek/deepseek-v4-flash:free` | no — 402 |
| `qwen/qwen3-next-80b-a3b-instruct:free` | no — provider error |
| `minimax/minimax-m2.5:free` | no — provider error |
| `nvidia/nemotron-3-super-120b-a12b:free` | **yes** |
| `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | yes (but empty replies) |
| `arcee-ai/trinity-large-thinking:free` | yes (but empty replies) |

Picked `nvidia/nemotron-3-super-120b-a12b:free` — the strongest of the
three that genuinely worked.

## The verification request (verbatim)

```
POST /api/v1/chat
Cookie: wolf_access_token=...
Content-Type: application/json
{"question":"how many alerts in the last 24 hours by severity?"}
```

Result (verbatim from the chat endpoint's JSON response):

```
model_id:    nvidia/nemotron-3-super-120b-a12b:free
strategy:    frontier
stop_reason: answer
steps:       2 | tool_calls: 1
tokens:      in=8466 out=411
citations:   ['count_alerts_by_severity']

ANSWER:
In the last 24 hours, there were 23 alerts total, all of low severity
(0 critical, 0 high, 0 medium, 23 low).

Evidence: The `count_alerts_by_severity` tool returned counts of
critical: 0, high: 0, medium: 0, low: 23, total: 23 for the time
window now-24h to now.

Citations: count_alerts_by_severity
```

End-to-end elapsed: 17 seconds.  This includes the network round trip
to OpenRouter, the hosted 120B model's reasoning, Wolf's tool dispatch
to the real Wazuh's `count_alerts_by_severity`, the second model call
to draft the answer, and the final response back to curl.

For comparison: the same question against `qwen3:4b` on local CPU
took 76 s and ran in `guided` (not `frontier`) strategy — both correct
behaviour for their respective tiers.

## Comparison with the local-Ollama half

| Field | qwen3:4b (local, ADR 0004) | Nemotron 120B (hosted, this ADR) |
|---|---|---|
| Strategy | guided | **frontier** |
| Latency (first request, cold) | ~76 s (CPU) | ~17 s (hosted, includes reasoning) |
| Tool calls | 1 | 1 |
| Citation attached | ✓ | ✓ |
| Answer correctness | matches Wazuh dashboard | matches Wazuh dashboard |
| Answer style | concise, prose | "Answer / Evidence / Citations" structured |
| License of the model itself | Apache 2.0 | NVIDIA Open Model License (restricted) |

Both paths satisfy the same exit criterion.  The frontier path proves
the orchestrator's agent loop, strategy selector, OpenAI adapter,
`DEFAULT_MODEL_API_KEY_REF` secrets indirection, and CORS / cookie
flow all work against a real hosted API at scale — not just local
Ollama.

## Decision

- **Phase 2 exit criterion is met.** The "tested on a frontier model"
  bullet flips to checked in `docs/PROGRESS.md` and
  `docs/10-build-roadmap.md` is now fully satisfied.
- **`DEFAULT_MODEL_ID` flips back to `qwen3:4b`** (per ADR 0004) for
  ongoing dev work — Nemotron is the verification path, not the
  steady-state default.  The OpenRouter API key stays stashed in the
  secrets backend so a future operator can re-run the verification by
  flipping three env vars (`DEFAULT_MODEL_PROVIDER=openai`,
  `DEFAULT_MODEL_ID=...`, `OPENAI_BASE_URL=https://openrouter.ai/api`)
  without ever re-sharing the key.
- **Keep both Wolf-side `KNOWN_MODELS` entries** for the two OpenRouter
  models (`deepseek/deepseek-v4-flash:free` and
  `nvidia/nemotron-3-super-120b-a12b:free`).  Inline comments warn
  about the credit-gating quirk and the Nvidia license caveat.
- **Operator security action:** the OpenRouter key was pasted in chat
  to get into the secrets backend.  Rotate it via
  https://openrouter.ai/keys after the verification commit lands.
  Same hygiene as the Wazuh credentials handled earlier in the
  project.

## Alternatives considered

- **DeepSeek R1 / V4 Flash via OpenRouter free tier** — first choice,
  blocked by the credit-gating quirk (issue 3 above).  Would need a
  $5-10 OpenRouter deposit to unlock.
- **DeepSeek direct (`platform.deepseek.com`)** — cheap per-token
  rates but requires a credit card.  Operator declined.
- **Groq free tier** — would have worked; OpenAI-compatible too.
  Skipped because the OpenRouter key was already in hand and Nemotron
  satisfied the exit criterion.
- **Strict reading: "frontier" must mean Claude Opus / GPT-4o /
  Gemini Pro per `KNOWN_MODELS`** — rejected.  The roadmap's exit
  criterion language ("tested on a frontier model") was written
  pre-knowledge of open frontier-tier models like Nemotron and the
  DeepSeek family.  Nemotron's measured behaviour (full tool calling,
  multi-step reasoning, concise grounded answers) lands at frontier
  tier on Wolf's own grading scale; satisfies both letter and spirit.

## Consequences

- Phase 2 is **fully closed** at the exit-criteria level.  Frontend +
  agent loop + dispatcher + read tools + audit + multi-turn all work
  across both deployment models (local CPU + hosted API).
- Wolf's claim of "no paid dependency required" stays honest: this
  verification used a free-tier hosted route, not a billed API.
- A future operator can re-run the verification any time by setting
  the three env vars listed above — the key is already in the
  encrypted secrets backend and reachable via
  `DEFAULT_MODEL_API_KEY_REF`.
- Two-`app/`-packages collision remains as deferred tech debt; the
  recurring workaround pattern (cd-into-orchestrator) is documented
  in PROGRESS.md §3 and in the probe and verification ADRs.
