---
name: model-switch-nemotron-after-slices
description: "OPERATOR INSTRUCTION (2026-07-06): AFTER slices 6-f.5 + 6-f.6 complete — if cohere/north-mini-code:free isn't right for agentic tasks/actions, switch the live chat model to Nemotron 3 Ultra (free), then Nemotron 3 Super (free) on OpenRouter"
metadata:
  type: project
---

**Instruction (2026-07-06):** "If Cohere is not the model that we are actually looking for to do this task and actions, try switching to Nemotron 3 Ultra (Free) and Nemotron 3 Super (free) instead, **after completing all the proposed and dedicated slices**" (= 6-f.5 unbounded persistence + disambiguation, then 6-f.6 deployment-aware config application).

**Why:** cohere/north-mini-code:free emitted its `propose_config_change` tool call as raw JSON prose in the 6-f.4 live probe (ADR 0031 free-model reality) — it researches fine but doesn't reliably complete agentic loops.

**How to apply:** after 6-f.6 ships — resolve the EXACT OpenRouter model IDs for "Nemotron 3 Ultra (free)" and "Nemotron 3 Super (free)" (NVIDIA Nemotron family; verify against the live OpenRouter catalog, don't guess from training data), probe native tool-calling empirically (the 2026-07-01 probe pattern), add graded `KNOWN_MODELS` entries in `models/interface.py`, then flip `DEFAULT_MODEL_ID` in `.env` (announce; operator's posture file). Try Ultra first, Super second. Free-tier caveats stay: shared 50 req/day account cap per [[model-failure-resilience-and-openrouter-free-reality]]; Ollama qwen3:8b remains the failover.
