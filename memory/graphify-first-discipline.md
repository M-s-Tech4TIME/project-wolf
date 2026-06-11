---
name: graphify-first-discipline
description: "STANDING RULE (2026-06-05) — when graphify-out/graph.json exists, use graphify query/path/explain FIRST for architectural questions, before grep/Read sweeps"
metadata: 
  node_type: memory
  type: feedback
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

STANDING RULE (2026-06-05): the project-wolf repo has a committed knowledge graph at `graphify-out/` that's kept current by a post-commit hook (`graphify hook install`). For architectural questions about the codebase, query the graph BEFORE falling back to grep / broad file reads.

**Why:** the user committed graphify-out/ explicitly so Claude Code sessions (mine + future ones + other developers') start with a current project map. The CLAUDE.md already has the rule ("For codebase questions, first run `graphify query` when graphify-out/graph.json exists"). The PreToolUse Bash hook also reminds me on every shell call. Despite all three signals I drifted back to grep + Read during the same session that committed the graph — exactly the kind of discipline gap the user flagged.

**How to apply:**

When the question is "How does X work?", "What calls Y?", "Trace Z through the codebase?", "What's the relationship between A and B?":
- FIRST: `graphify query "<the question phrased naturally>"` — returns a scoped subgraph, usually much smaller than the raw grep output.
- For named-relationship questions: `graphify path "A" "B"` — shortest path.
- For "explain this concept": `graphify explain "X"` — connected neighborhood.

For cross-session catch-up at session start:
- Read `graphify-out/GRAPH_REPORT.md` for god nodes + community structure + hyperedges. Faster than re-reading 53 planning docs.
- The graph's god nodes are the load-bearing abstractions: `OrganizationContext`, `NativeToolCalling`, `WolfError`, `Citation`, `Message`, `ToolExecContext`, `AgentStrategy`, etc.

For drift detection (pairs with [[periodic-plan-sync]]):
- Compare graph communities + hyperedges against `docs/10-build-roadmap.md` + `docs/17-release-engineering.md` after major slices.
- The graph is "what we have"; the docs are "what we say we have". A diff between the two surfaces drift.

When NOT to use the graph:
- Need exact file contents / line numbers → Read tool.
- Need to grep a specific string literal → Bash grep (faster + the graph doesn't index every string).
- Running tests / git operations / shell ops → Bash (graph is read-only).
- Single targeted lookup of one symbol → Bash grep (sub-second; graph startup not worth it).

The graph is the map, not the territory. Use it for navigation; use direct tools for action.

Related: [[periodic-plan-sync]] (audit roadmap/arch for drift between phase transitions — graph is one of the inputs).
