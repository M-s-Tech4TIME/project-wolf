# Handoff prompt — new dev machine

> **STATE SNAPSHOT — last edited 2026-05-29** (Phase 2 close-out).
> The repo has moved through Phases 3 → 4 → 5-prep → 5.4 (Native
> HTTPS + wolf-cert) → 5.5 (component renaming) since this was
> written. The path strings inside this document were patched on
> 2026-06-03 to reflect the post-Phase-5.5 component layout
> (`services/dashboard/`, `services/server/`, `wolf-server`,
> `wolf-dashboard`), but the narrative below still describes
> Phase 2's state ("128 backend tests", etc.) which is
> significantly outdated.
>
> **For current state always read `docs/PROGRESS.md` first** — it's
> the only source of truth that's kept current per-slice. This
> handoff doc is operationally usable for first-machine setup
> (the path strings are right) but its "where we are" narrative
> needs a fresh rewrite at some point.

**Purpose.** When you (the project owner) sit down at a freshly-set-up
dev machine, clone this repo, and open Claude Code in the directory,
paste the prompt block below as your first message. It briefs the new
Claude Code session on what to read, what state it's inheriting, what
the next concrete work is, and what guardrails to respect.

**When to use.**
- Moving to a different physical machine (GPU box, laptop swap, server).
- Starting a fresh Claude Code session weeks/months later when you've
  forgotten where you left off.
- Onboarding a new collaborator who will pair with Claude Code.

**How to use.**

1. `git clone git@github.com:M-s-Tech4TIME/project-wolf.git`
2. `cd project-wolf`
3. Open Claude Code in that directory.
4. Paste the entire fenced block below as your first message.
5. Wait for Claude's summary-back before approving setup.

**Maintenance.** This prompt assumes a state that drifts as the project
moves forward. Update it whenever:

- The phase changes (Phase 2 → 3 → 4...).
- The default model changes.
- A new ADR shifts the "what's next" picture.
- A new gotcha or recurring footgun emerges that the new session
  should know about up-front.

The prompt's effectiveness depends on staying truthful. Treat it like
[`docs/PROGRESS.md`](../docs/PROGRESS.md): edit freely as state changes.

---

## The prompt — copy from the opening backtick to the closing backtick

```
You are picking up an in-progress project called Wolf — an open-source,
model-agnostic, agentic AI platform for Wazuh. I just cloned the repo
fresh onto this machine. This is a brand-new dev environment for you:
new host, new session, no .env, no .local/, no secrets. The previous
session ran on a different VM and ended at the current HEAD on main.

Before you do anything else, read these in order:

  1. ONBOARDING.md at repo root — written specifically for you.
     Covers setup from a clean clone, system requirements, gotchas,
     troubleshooting, and a file-location reference.
  2. docs/PROGRESS.md — live project state. Tells you exactly what
     exists, what works, and what's next.
  3. docs/CHANGELOG.md — read the top 2–3 entries to understand the
     most recent sessions.
  4. docs/decisions/README.md plus every ADR it lists (0001–0008).
     The ADRs are why things are the way they are. Pay particular
     attention to ADRs 0006, 0007, and 0008 — they set the strategic
     posture you must follow.
  5. docs/15-supported-model-matrix.md — the four-family commitment
     that drove the move to this GPU-equipped machine.
  6. docs/16-distribution-and-packaging.md — the native delivery
     channel spec. Read the §"Development against this channel"
     section before setting up Postgres.

After you've read those, the state in one paragraph:

Phase 2 is closed (ADR 0005). The agent loop works end-to-end on both
a local Ollama model (qwen3:4b, the steady-state default per ADR 0004)
and a hosted frontier-tier model (Nemotron 120B via OpenRouter). 9 of
9 read tools verified live. 128 backend tests passing. mypy strict
clean. wolf-dashboard (Next.js 16) functional. ADR 0006 commits Wolf to
natively supporting four model families locally (Qwen 3, Llama 3,
Gemma 3, GLM 5.1 ~32B); four probe ADRs are pending the GPU hardware
you are now running on. ADR 0007 + ADR 0008 commit Wolf to native
.deb/.rpm + systemd + install-script delivery as the PRIMARY channel;
Docker is supplementary (baseline-supported, not promoted). Dev uses
system Postgres to match that posture.

Your concrete next work, in priority order:

  A. Get the dev environment running. Follow ONBOARDING.md §3 step
     by step — note that §3.4 leads with SYSTEM Postgres install
     (PostgreSQL APT/YUM repo + apt/dnf install + createrole +
     createdb + CREATE EXTENSION vector). Docker Postgres is a
     supported alternative but not the recommended path; use system
     Postgres unless you have a specific reason not to. Install uv,
     Node 24, Ollama; uv sync --all-packages; npm install in
     /services/dashboard/; generate the two dev secrets and write .env; run
     alembic migrations; bootstrap a tenant (bootstrap_tenant
     requires Wazuh fields — use the "no Wazuh yet" placeholder
     pattern in §3.9 if you don't have a Wazuh handy); start
     wolf-server and wolf-dashboard. Verify with §4 (make check +
     curl-driven chat).

  B. Confirm everything passes: `make check` (128 tests + lint +
     typecheck strict). If anything fails on this machine that was
     passing on the previous VM, that's a real signal — surface it
     before moving on, don't paper over it.

  C. Pull the family sizes this hardware can actually run.
     The dev machine is a laptop with an NVIDIA RTX 4050 Laptop
     GPU (6 GB VRAM, Profile B tight end per docs/13). At Q4_K_M
     the usable VRAM budget is ~4.5-5 GB after KV cache + display
     server overhead. Pull these five:

       ollama pull qwen3:4b      # Apache 2.0, steady-state default
       ollama pull qwen3.5:4b    # NEW: Qwen 3.5 released ~late May 2026.
                                  # Treated as a Qwen-3-family variant
                                  # per ADR 0006. License unverified
                                  # from Ollama page — confirm Apache
                                  # 2.0 before adding to KNOWN_MODELS.
                                  # 256K context window (vs Qwen 3's
                                  # 128K) may help Phase 3 RAG.
       ollama pull gemma3:4b     # Gemma family coverage
       ollama pull llama3.2:3b   # Llama family coverage
       ollama pull qwen3:8b      # TIGHT FIT — may need reduced ctx;
                                  # only one tight-fit model loaded at
                                  # a time (use `ollama ps` to verify
                                  # PROCESSOR is 100% GPU not CPU).

     Do NOT pull qwen3:14b, qwen3:32b, qwen3.5:9b (was 8B in Qwen 3,
     grown to 9B in 3.5 — exceeds 6 GB), qwen3.5:27b+, gemma3:12b,
     gemma3:27b, glm-5.1, or llama3:70b on this hardware. They will
     download (6-80 GB each) then fail to load because they exceed
     VRAM. Those probe ADRs remain blocked on workstation-class GPU
     hardware (24+ GB VRAM).

     Before pulling, confirm Ollama sees the GPU:
       nvidia-smi             # should list the RTX 4050 Laptop
       ollama run qwen3:4b "hi"
       ollama ps              # PROCESSOR column must say "100% GPU"

     If PROCESSOR shows CPU, the NVIDIA driver is missing or too
     old. Fix that first (sudo ubuntu-drivers install; reboot) —
     otherwise every probe will measure CPU latency, not GPU.

  D. Run capability probes against the new sizes. Pattern:
       uv run python -m tools.model_probe --provider ollama --model <name>
     Capture the output, then write one ADR per family/size
     combination — follow the ADR 0001/0002/0003 template exactly
     (status: accepted; full probe transcript; reasoning_tier and
     recommended_strategy decision; KNOWN_MODELS entry amendment
     if measured capability differs from the static estimate).
     The next available ADR number is 0009. Expect three genuinely
     new probes on this hardware:
       - qwen3:8b on GPU (was unprobed; now possible at tight fit)
       - qwen3.5:4b on GPU (newly released Qwen 3.5; verify license
         is Apache 2.0 before writing the KNOWN_MODELS entry; if the
         probe shows it matches or beats qwen3:4b's ADR 0002 results,
         consider a follow-up ADR flipping DEFAULT_MODEL_ID from
         qwen3:4b → qwen3.5:4b — same pattern as ADR 0004's
         llama3.2 → qwen3:4b switch)
       - qwen3.5:4b cross-comparison vs qwen3:4b on the same hardware
         (Qwen 3 ran on CPU per ADR 0002; this is the first chance
         to compare them on equal GPU footing)
     gemma3:4b was already probed on CPU (ADR 0003); a GPU re-probe
     is optional and mostly just shows faster latency. llama3:8b is
     optional (skip if qwen3:8b already saturates the tight-fit
     VRAM budget).

  E. Optional regression guard: run `make up` once to confirm the
     supplementary container channel still builds and runs on this
     hardware. This is the cheap check ADR 0008 calls for to
     prevent Docker bit-rot. If it fails, surface the failure but
     don't fix it as a side-quest — note it for follow-up.

  F. After the probes: either start Phase 3 (RAG + grounding
     validator per docs/06 and docs/10) or address whichever Phase
     2 leftover is highest leverage. Check with me before committing
     to either direction.

Important constraints (do not skip):

  - Read ONBOARDING.md §6 (gotchas) before launching uvicorn. The
    two-app/-packages collision will bite you if you start uvicorn
    from repo root. Always cd services/server first.
  - System Postgres on this machine starts via systemd at boot;
    you do not need `docker compose up -d postgres`. Verify with
    `sudo systemctl status postgresql`.
  - .local/secrets.enc is gitignored and lives only on the previous
    VM. You start with an empty secrets backend on this machine.
    The OpenRouter API key from ADR 0005 is NOT here; if I need
    to re-run the hosted-API verification, I will re-stash it via
    the set_secret CLI pattern documented in ONBOARDING.md §5
    "Use a hosted API instead of Ollama."
  - Native is the primary delivery channel (ADR 0008). Do not add
    Docker-specific code paths during development. The code stays
    distro-agnostic (env-driven config, no hard-coded container
    paths, management CLIs usable as plain `python -m`, wolf-dashboard
    on Next.js `output: 'standalone'`). See doc 16 §"How current
    code should accommodate this commitment" for the full list.
  - End-of-session protocol per docs/11: update docs/PROGRESS.md,
    append an entry to docs/CHANGELOG.md, commit. Non-negotiable.
  - Do not push to origin/main without checking with me first.

When you're done reading the above docs, summarize back to me in
under 100 words: (a) the current commit you are at, (b) what you
understand the immediate next step to be, (c) any inconsistencies
you noticed between ONBOARDING.md and the actual repo (the previous
session may have missed something — surface it).

Then wait for my go-ahead before starting setup.
```
