---
name: conversation-tree-persistence-plan
description: "When the DB-storage phase lands, this is the persistence design for the conversation tree (Slice 5.0c-l v4) — schema, write rules, integrity, round-trip test."
metadata: 
  node_type: memory
  type: project
  originSessionId: 5cd03513-6614-4694-a862-5bd7c8534b36
---

**Captured 2026-06-02 during Slice 5.0c-l v4.1 — to be implemented when the project's DB-persistence phase lands. Until then, conversations live in-memory only.**

Why this exists: the user explicitly asked me to note the plan now so nothing gets missed when the persistence phase begins. The tree-branching architecture has tight requirements — naïve schemas can silently drop off-branch subtrees or scramble sibling order on reload, both of which would invalidate the v4 refactor we just landed.

## Frontend shape (the source of truth for what must round-trip)

Each conversation is a tree of message nodes:

- `Conversation { id, title, nodes: Record<id, MessageNode>, root_children: string[], selected_root_id: string | null, created_at, updated_at, starred? }`
- `UserMessageNode  { id, parent_id (string | null), role: "user", content, children: string[], selected_child_id: string | null, created_at }`
- `AssistantMessageNode { id, parent_id (string, never null), role: "assistant", content, children, selected_child_id, created_at, + run metadata: citations, tool_events, stop_reason, loop_id, strategy, model_id, step_count, tool_call_count, input_tokens, output_tokens, started_at, completed_at, grounding_supported, grounding_unsupported, grounding_uncertain, grounding_unverifiable }`

Why: covered in [[branching-architecture]] — each node owns its own `children` array; sibling sets are LOCAL to a parent and never shared across fork points.

## Persistence schema (proposed)

**Two tables per organization** (organization scoping per the standing rules):

```
conversations
  id                 UUID PRIMARY KEY
  organization_id          UUID NOT NULL  → multi-organization scoping
  user_id            UUID NOT NULL  → owner of the chat
  title              VARCHAR
  selected_root_id   UUID NULLABLE  → FK to message_nodes.id (NOT enforced — see deferred constraint)
  created_at         TIMESTAMPTZ
  updated_at         TIMESTAMPTZ
  starred            BOOLEAN DEFAULT false

message_nodes
  id                 UUID PRIMARY KEY
  conversation_id    UUID NOT NULL  → FK to conversations.id, ON DELETE CASCADE
  parent_id          UUID NULLABLE  → self-FK to message_nodes.id, ON DELETE CASCADE
  role               VARCHAR(16) NOT NULL  ('user' | 'assistant')
  content            TEXT NOT NULL
  selected_child_id  UUID NULLABLE  → self-FK to message_nodes.id (NOT enforced as FK — see below)
  position           INTEGER NOT NULL  → ordering within siblings (see "Sibling order" below)
  created_at         TIMESTAMPTZ NOT NULL
  -- Assistant-only fields (NULLABLE on user rows):
  citations          JSONB
  tool_events        JSONB
  stop_reason        VARCHAR(32)
  loop_id            VARCHAR
  strategy           VARCHAR(32)
  model_id           VARCHAR(100)
  step_count         INTEGER
  tool_call_count    INTEGER
  input_tokens       INTEGER
  output_tokens      INTEGER
  started_at         TIMESTAMPTZ
  completed_at       TIMESTAMPTZ
  grounding_supported    INTEGER NULLABLE
  grounding_unsupported  INTEGER NULLABLE
  grounding_uncertain    INTEGER NULLABLE
  grounding_unverifiable INTEGER NULLABLE
```

**Indexes:**
- `message_nodes(conversation_id, parent_id, position)` — drives the children query at load time.
- `message_nodes(conversation_id)` — load-all-by-conversation for the tree reconstruction.
- `conversations(organization_id, updated_at DESC)` — sidebar listing.

## Critical write rules

These are the rules that prevent the bug the user reported (silent overwrite / vanished content). They mirror the in-memory invariants already enforced by `lib/branches.ts`:

1. **Every node is its own row.** Adding a version (Edit / Retry) is `INSERT INTO message_nodes`, never `UPDATE` of an existing row's `content`. Past versions stay byte-identical forever.
2. **Atomic version-add transaction.** Wrap both the `INSERT` of the new node AND the `UPDATE` of the parent's `selected_child_id` in one transaction. A crash mid-way must leave EITHER both intact OR neither. No dangling `selected_child_id` pointing at a non-existent row; no orphan node with no parent reference.
3. **No path flattening.** Save EVERY node in the tree — every sibling, every off-path subtree. Do NOT serialise "just the active path." This is the most common failure mode for branching chat UIs.
4. **Lossless round-trip.** Reading back must reproduce the tree byte-identically: same node ids, same `parent_id`, same `selected_child_id`, same sibling ordering, same content, same metadata.

## Sibling order

Two options; pick one and stay with it:

- **Option A — `position` integer.** Explicit ordering per parent. Append assigns `MAX(position WHERE parent_id = X) + 1`. Cleanest semantics.
- **Option B — order by `created_at`.** Implicit ordering. Simpler schema but assumes the clock is monotonic and unique within a millisecond (use a sequence or a tie-breaker if not).

Recommend Option A — explicit `position`. Already in the schema above.

## Referential integrity

- `parent_id` is a self-FK with `ON DELETE CASCADE` — deleting a node drops its whole subtree.
- `selected_child_id` is INTENTIONALLY NOT a FK constraint. Reason: a transaction inserting a new child first writes the child row, then updates the parent's `selected_child_id`. Within the transaction this is fine. But for snapshot-isolation paranoia, having it as a non-FK pointer plus a validation step on read is safer. Validate on load: if `selected_child_id` references a missing row OR doesn't match any actual child, default to `children[0]` rather than crashing. Log the inconsistency.
- `selected_root_id` on conversations: same treatment. NOT a FK; validated on load.

## Organization scoping

Per [[integrity-across-the-stack]]: every read query MUST include `organization_id = :requesting_organization`. Routes through `OrganizationScopedQueryBuilder`. Cross-organization tests in the orchestrator's isolation suite must add cases for the conversation/message_nodes tables.

## Required round-trip test

`tests/test_conversation_tree_persistence.py` (orchestrator-side, after the backend service for this lands):

1. Build a conversation in memory with a multi-version retry-fork (`u1` with `children = [a1, a2, a3]`) plus a separate edit-fork at the root (`root_children = [u1, u1']` and `u1'` has its own assistant child).
2. Persist via the service.
3. Drop the in-memory store / open a fresh DB session.
4. Reload.
5. Assert deep equality: same node ids, same `parent_id` per node, same `children` arrays (preserving order), same `selected_child_id` per node, same `selected_root_id` on the conversation, same content per node, same assistant-only metadata.
6. Switch `selected_root_id` from `u1'` to `u1`. Persist. Reload. Assert the selection switched but every node's content + structure is unchanged.

## Frontend-side adapter notes

When this lands, `chat-shell.tsx`'s in-memory `setConversations` calls become async — each mutation hits the persistence service. The user-facing UX should remain optimistic: write to in-memory state immediately, fire-and-forget the persist; surface any persistence error as a non-blocking toast. The fork primitive (`lib/branches.ts`) already produces a pure-functional new conversation object — a service adapter can serialise the delta (one new node + one parent-children-update) instead of re-serialising the whole tree.

## Cross-references

- [[integrity-across-the-stack]] — every services/ change runs the full cross-organization isolation gate; this schema's reads must too.
- [[no-unaddressed-errors]] — load-time validators that detect a dangling `selected_child_id` MUST log + heal, not silently drop the subtree.
- [[quality-secure-coding-discipline]] — DB-side: parameterised queries, no string concatenation; per-organization scoping at the query-builder level, not at the application layer.
