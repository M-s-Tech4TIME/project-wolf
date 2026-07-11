# Claude Code — resume prompt for Project Wolf

> Copy everything inside the fenced block below and paste it as the first
> message of a fresh Claude Code session started in the repo root
> (`project-wolf/`). It boots the session from cold straight into
> productive, rule-compliant work. Written 2026-07-12 at the development
> pause — see [HANDOVER.md](HANDOVER.md) for the full context it points at.

```text
You are resuming Project Wolf — a self-hosted, multi-organization agentic
AI layer for Wazuh (components: wolf-server :7860, wolf-dashboard :3000,
wolf-database :5432, wolf-search :1307). Development was deliberately
paused on 2026-07-12 with everything shipped and CI green; you are picking
it back up. Do the following, in this exact order, before writing any code:

1. ORIENT — read these four files completely:
   - docs/HANDOVER.md        (the wrap-up: state, queue, rules, credentials)
   - docs/PROGRESS.md        (live state ledger, newest first — read at least
                              the top entry and skim the last month)
   - memory/MEMORY.md        (standing-rule + decision index; open any topic
                              file whose one-liner is relevant to the task)
   - docs/10-build-roadmap.md (skim the phase list; read the next open phase)
   If your persistent memory directory is empty or stale, re-seed it from
   the in-repo memory/ mirror (they are kept byte-identical by standing rule).

2. VERIFY THE ENVIRONMENT — before trusting anything:
   - git status / git log --oneline -5 (expect a clean tree at the wrap-up
     commit or later; if the tree is dirty, ask the operator before touching it)
   - systemctl --user status wolf-server wolf-dashboard; systemctl status
     wolf-search; curl -k https://127.0.0.1:7860/api/v1/auth/login (401 = up)
   - psql connectivity per .env DATABASE_URL (PostgreSQL 18 + pgvector expected)
   - ollama list (expect qwen3:8b, qwen3-embedding:latest, nomic-embed-text-v2-moe)
   - If the machine is a FRESH clone instead: follow ONBOARDING.md end-to-end
     first (system requirements → wolf-database → .env → migrations →
     bootstrap_organization → seed knowledge → services). Credentials
     templates: credentials.example/ → copy to credentials/ and have the
     operator fill in real values.
   - Regenerate the knowledge graph if graphify-out/graph.json is missing
     (/graphify skill or first post-commit hook run), then prefer graphify
     query/path/explain for architectural questions.

3. PROVE THE GATES — run and require green before starting work:
   - uv run ruff check .            (repo root)
   - make typecheck                 (mypy --strict set)
   - cd services/server && uv run pytest -q   (expect 963+ passed, 0 skips)
   Any failure is fixed at the root before new work (standing rule:
   no unaddressed errors, no skips of any kind, ever).

4. RESUME THE QUEUE — docs/HANDOVER.md §3 is authoritative. At pause it was:
   (1) 6-f.4 operator web-test: virustotal <integration> upsert end-to-end
       on the live cluster — needs the wazuh-wui credential and qwen3:8b as
       chat model; coordinate with the operator, it is THEIR test.
   (2) Nemotron model-switch evaluation (operator-gated — ask before starting).
   (3) Phase 6.9 SMTP → 6.7 notifications → 6.8 SSE push.
   (4) Phase 6.10 config-settings system (ADR 0019).
   (5) Phases 6.11/6.12, then 6.13 grounding enrichment (operator-sequenced).
   Confirm the pick with the operator if their first message doesn't name one.

5. RULES IN FORCE — the full digest is HANDOVER.md §4; the ones you will
   hit immediately: work in slices with one commit per slice direct to main;
   CI audit before EVERY push and watch the run to green via background
   gh run watch; full backend suite + cross-org isolation gate on every
   services/ change; zero skips; memory changes mirrored byte-identical
   between ~/.claude/.../memory/ and repo memory/; restart wolf-server only
   via systemctl --user restart wolf-server.service; never accept the sudo
   password — privileged steps use an announced temporary sudoers grant,
   removed and verified afterward; per-slice web-test checkpoint with the
   operator before calling a slice done.

Report back: environment status, gate results, and which queue item you're
starting — then start it.
```
