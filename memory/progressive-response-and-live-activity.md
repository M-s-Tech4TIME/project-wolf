---
name: progressive-response-and-live-activity
description: Slice 5.0c UI intent — answer streams token-by-token (Claude-style) and the step indicator narrates what Wolf is actually doing
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

User asked (2026-05-28) for two UX behaviors in Slice 5.0c so Wolf *feels* alive instead of "send → 3 minute hang → wall of text appears."

## (1) Progressive answer rendering ("token-by-token like Claude")

When Wolf's final answer is ready, the text should reveal **smoothly word-by-word / token-by-token**, not as a single instantaneous block. Reference: how `claude.ai` and the Claude Code TUI render responses.

**Two viable implementations:**

| Approach | Pros | Cons |
|---|---|---|
| **(a) Real backend streaming**: switch the chat path to use `/api/v1/chat/stream` (already exists, SSE-based) and have the OllamaAdapter emit tokens via Ollama's `stream: true` mode (today's `OllamaAdapter.stream()` just yields the whole content at once — line 137 of [`ollama.py`](file:///home/alsechemist/Codespace/project-wolf/services/orchestrator/app/models/ollama.py)). | True low-latency feel; user sees the first word in seconds, not minutes. | Backend change touching the model adapter; needs care with the existing audit/grounding flow that operates on the *finished* answer. |
| **(b) Frontend typewriter**: keep non-stream chat; once the final answer arrives, the React markdown component reveals characters with a small per-char delay. | Tiny change, no backend touch, works today. | Doesn't help the long pre-answer wait — typing only starts after the full answer is ready. |

**Recommendation for 5.0c:** Start with **(a) real streaming** — it's the meaningful UX win. (b) is a stop-gap if streaming proves bigger than budgeted. Grounding still runs on the completed answer (the validator already operates post-stream), so the streaming + grounding badges flow stays coherent.

## (2) Live activity feed during steps

The current UI shows just "Step 1/8" with a spinning icon. The user wants the step indicator to **narrate what Wolf is actually doing right now**, dynamically and naturally — *"Asking the model…"*, *"Searching Wazuh for SSH brute-force alerts…"*, *"Read 44 alerts, summarising…"*, *"Asking the grounding judge…"*, *"Drafting the answer…"*

**Foundation already exists.** The agent loop emits these SSE events (see [`agent/loop.py`](file:///home/alsechemist/Codespace/project-wolf/services/orchestrator/app/agent/loop.py) — `_emit` calls): `loop.started`, `step.started`, `model.call.completed`, `tool.call.completed`, `grounding.completed`, `answer`. The frontend `useChatStream` hook ([`hooks/use-chat-stream.ts`](file:///home/alsechemist/Codespace/project-wolf/frontend/hooks/use-chat-stream.ts)) already consumes them.

**What's missing:** mapping each event to a human-readable activity line, and a `started` event for the operations that currently only emit `completed`. Likely additions:

| Event | UI line |
|---|---|
| `step.started` | *"Step N: thinking…"* (vary by step index) |
| `tool.call.started` (NEW) | *"Searching Wazuh: `{tool_name}` ({args summary})…"* |
| `tool.call.completed` | *"Got {result_count} {tool_name} results, reading them…"* |
| `model.call.completed` | *"Model produced a response, deciding next step…"* |
| `grounding.started` (NEW) | *"Validating claims with the grounding judge…"* (the slow step on this GPU) |
| `grounding.completed` | *"Validated: {sup}✓ {unc}⚠ {unsup}✗"* |
| `answer` | *"Done."* |

Phrasing should be natural and varied — not robotic. A small pool of variants per event so the same phrase doesn't repeat back-to-back. The user explicitly called for the feed to feel **robust and dynamic**, not scripted.

## Out of scope until 5.0c lands

The current Slice 5.0c queue is large (UI overhaul + 4-chip rename + these two). If during implementation either of these proves to be its own sub-slice, split it (5.0c-a, 5.0c-b…) and keep the per-slice web-test workflow ([[per-slice-web-test-checkpoints]]).
