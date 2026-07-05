---
name: web-research-as-universal-power
description: FOUNDATIONAL DIRECTIVE (2026-07-05) — web research is Wolf's universal power, not a config-authoring accessory; research-to-act like Claude — learn any unknown procedure from valid sources in-session, then act through the capability-gated write paths
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

Operator directive (2026-07-05, delivered while opening slice 6-f.4, right after passing the 6-f.3 web-test): `web_search`/`web_fetch`/`web_crawl` are **not** just for config authoring, and not just search-and-summarize-for-the-user. Wolf must use its native web-research capability **to the fullest, as its own power — its go-to tool** for all kinds of operations: security operations, active response, integrations, detection engineering, everything in its ecosystem. When Wolf doesn't know how to do something, it doesn't stop — it **researches the web, gains the knowledge from valid sources with references, "trains itself" in-session, and then executes the task** — explicitly "just as Claude does": research → learn → verify → act. "I want Wolf to be unstoppable."

**Why:** the operator's bar for Wolf is Claude-grade agentic capability (same bar as [[wolf-uiux-claude-grade-standard]] for UI). A Wolf bounded by its trained knowledge is artificially limited; a Wolf that can research-then-act is bounded only by its credential's RBAC — the same principle as [[wolf-unrestricted-full-power]] (restriction comes from Wazuh RBAC, never from Wolf limiting itself; now also never from the limits of trained knowledge).

**How to apply:**
- **Every capability slice from 6-f.4 onward:** treat web research as a first-class step in ANY task flow, not a standalone Q&A tool. Agent prompts (WEB_RESEARCH_SUFFIX / strategy prompts) must teach research-to-act: unknown procedure → research docs-first → confirm understanding → act via the existing propose/approve/execute paths.
- **Learning stays provenance-backed:** citations (url + source tier), untrusted-content envelope (web text = data, never instructions), grounding verdicts on research-derived claims. "Train itself with valid source and references" — provenance is what makes it safe.
- **The authority model is UNCHANGED:** research informs; RBAC capability checks + the approval gateway still gate every write (ADR 0025/0029). Research-to-act ≠ research-to-bypass.
- When scoping any future feature, ask "can Wolf research its way to doing this better/at all?" — the web tools amplify every other tool.

Recorded in the ADR 0032 2026-07-05 addendum + roadmap 6-f intro. Related: [[web-research-phase]], [[grounding-enrichment-tools-future-phase]] (web evidence joining the judge is the verification half of this directive).
