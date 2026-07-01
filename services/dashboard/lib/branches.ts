/**
 * Slice 5.0c-l (refactor 2026-06-02) — node-tree branching helpers.
 *
 * A conversation is a TREE of message nodes (user + assistant
 * alternating along any root-to-leaf path). Each node owns its own
 * `children: string[]` and `selected_child_id: string | null`. The
 * conversation also stores `root_children` + `selected_root_id` for
 * the top-level user-message layer.
 *
 * The single most important invariant:
 *
 *   A new version (Edit or Retry) is appended to EXACTLY one array:
 *   `target.parent_id`'s children. Never any other ancestor's. Two
 *   distinct fork points never share a sibling set.
 *
 * Edit and Retry both reduce to one primitive — `fork(conversation,
 * target_id, new_node)` — defined below. It computes the fork parent
 * from `target.parent_id` directly and writes only there.
 */

import type {
  AssistantMessageNode,
  Conversation,
  ConversationTurn,
  MessageNode,
  UserMessageNode,
} from "./types";

// ────────────────────────────────────────────────────────────────────────────
// Read-side helpers — walk the active path, list siblings, build history.
// ────────────────────────────────────────────────────────────────────────────

/**
 * The nodes along the currently-displayed branch, in render order
 * (root → leaf). The path strictly alternates user/assistant so
 * long as the tree was built via this module's mutators.
 */
export function activePathNodes(conversation: Conversation): MessageNode[] {
  const path: MessageNode[] = [];
  let cur_id: string | null = conversation.selected_root_id;
  while (cur_id !== null) {
    const node = conversation.nodes[cur_id];
    if (!node) break;
    path.push(node);
    cur_id = node.selected_child_id;
  }
  return path;
}

/**
 * The deepest node on the active path, or null for an empty
 * conversation. Used by chat-shell to find the parent for a fresh
 * user turn (which always appends as a child of the current leaf).
 */
export function activeLeaf(conversation: Conversation): MessageNode | null {
  const path = activePathNodes(conversation);
  return path.length > 0 ? path[path.length - 1] : null;
}

/**
 * The ordered sibling-group `node_id` belongs to (including itself).
 * Returned in insertion order so the navigator's `1/N … N/N` numbering
 * stays stable as branches accumulate.
 *
 * Top-level user messages live in `conversation.root_children`;
 * everyone else lives in `nodes[parent_id].children`. Either way the
 * sibling set is LOCAL to that one container — never merged with
 * any other branch point's children.
 */
export function siblingsOfNode(
  conversation: Conversation,
  node_id: string,
): MessageNode[] {
  const node = conversation.nodes[node_id];
  if (!node) return [];
  const sibling_ids =
    node.parent_id === null
      ? conversation.root_children
      : conversation.nodes[node.parent_id]?.children ?? [];
  const out: MessageNode[] = [];
  for (const id of sibling_ids) {
    const s = conversation.nodes[id];
    if (s) out.push(s);
  }
  return out;
}

/**
 * Linear `ConversationTurn[]` history representing the active path
 * up to (but NOT including) the node identified by `exclusive_of_id`.
 * Passed to wolf-server as the model's context window.
 *
 * If `exclusive_of_id` is null, the full active path is returned —
 * used for a fresh turn at the tip (no fork; the new turn extends
 * the existing leaf).
 */
export function historyUpTo(
  conversation: Conversation,
  exclusive_of_id: string | null,
): ConversationTurn[] {
  const turns: ConversationTurn[] = [];
  for (const node of activePathNodes(conversation)) {
    if (node.id === exclusive_of_id) break;
    turns.push({ role: node.role, content: node.content });
  }
  return turns;
}

// ────────────────────────────────────────────────────────────────────────────
// Write-side primitives — the only safe way to mutate the tree.
// ────────────────────────────────────────────────────────────────────────────

/**
 * The single primitive both Edit and Retry route through.
 *
 *   fork(conversation, target_id, new_node)
 *
 * appends `new_node` as a sibling of `target_id` — i.e., a child of
 * `target.parent_id`. The parent's `selected_child_id` (or the
 * conversation's `selected_root_id` for top-level nodes) is updated
 * to point at the new node so the active path follows the new
 * branch immediately. The previous branch's subtree is preserved
 * verbatim and remains reachable via the `< N/M >` navigator at
 * this fork.
 *
 * INVARIANT: this function writes to exactly one children array
 * (`target.parent_id`'s). The target's own children are never
 * touched. No code path in this module reaches for another
 * ancestor.
 *
 * Pre-condition: `new_node.parent_id === target.parent_id`. The
 * function asserts this and throws on mismatch — a guard against
 * future regressions of the very bug this refactor exists to fix.
 */
export function fork(
  conversation: Conversation,
  target_id: string,
  new_node: MessageNode,
): Conversation {
  const target = conversation.nodes[target_id];
  if (!target) {
    throw new Error(`fork: target node ${target_id} not in conversation`);
  }
  if (new_node.parent_id !== target.parent_id) {
    throw new Error(
      `fork invariant violation: new_node.parent_id (${new_node.parent_id}) !== target.parent_id (${target.parent_id})`,
    );
  }
  return appendChildOf(conversation, target.parent_id, new_node);
}

/**
 * Append `new_node` as a fresh child of `parent_id` (or as a new
 * top-level root if `parent_id === null`). Used by:
 *
 *   - The fork primitive (above): parent_id = target.parent_id.
 *   - Fresh-turn submit: parent_id = active leaf's id (a user node
 *     appended under the previous assistant, or an assistant node
 *     appended under the previous user).
 *
 * The new node's `parent_id` field must match `parent_id`; the
 * function asserts this so the tree's pointers stay self-consistent.
 *
 * Side effects on the conversation:
 *   1. `nodes[new_node.id] = new_node`
 *   2. Parent's `children` += new_node.id
 *   3. Parent's `selected_child_id = new_node.id`     ←┐ active path
 *      (or conversation's `selected_root_id = new_node.id`)  ┘ follows
 *
 * INVARIANT: writes to exactly one array — `parent_id`'s `children`
 * (or `root_children` for the top level). No ancestor walking.
 */
export function appendChildOf(
  conversation: Conversation,
  parent_id: string | null,
  new_node: MessageNode,
): Conversation {
  if (new_node.parent_id !== parent_id) {
    throw new Error(
      `appendChildOf invariant violation: new_node.parent_id (${new_node.parent_id}) !== parent_id (${parent_id})`,
    );
  }
  // Slice 5.0c-l v4.1 — id-uniqueness guard. A duplicate id would
  // silently OVERWRITE an existing node via the spread below,
  // erasing its content. Better to throw loudly: this can only
  // happen via a coding regression upstream (e.g. someone reusing a
  // backend field as the node id), and an early failure is far
  // easier to diagnose than a "Wolf's previous response vanished"
  // report from a user weeks later.
  if (conversation.nodes[new_node.id]) {
    throw new Error(
      `appendChildOf invariant violation: node id ${new_node.id} already exists — ids must be unique`,
    );
  }
  const next_nodes = { ...conversation.nodes, [new_node.id]: new_node };
  if (parent_id === null) {
    return {
      ...conversation,
      nodes: next_nodes,
      root_children: [...conversation.root_children, new_node.id],
      selected_root_id: new_node.id,
    };
  }
  const parent = conversation.nodes[parent_id];
  if (!parent) {
    throw new Error(`appendChildOf: parent node ${parent_id} not in conversation`);
  }
  return {
    ...conversation,
    nodes: {
      ...next_nodes,
      [parent_id]: {
        ...parent,
        children: [...parent.children, new_node.id],
        selected_child_id: new_node.id,
      },
    },
  };
}

/**
 * Switch the active branch at `target_id`'s fork to its sibling
 * `new_sibling_id`. Both ids MUST share a parent (asserted). Writes
 * to exactly one selection field — the parent's `selected_child_id`
 * (or conversation's `selected_root_id` for the top level). Every
 * other branch point on the tree is left untouched: their
 * `selected_child_id` selections persist, so a subsequent walk down
 * from `new_sibling_id` follows whatever sub-branch was active on
 * that side previously.
 */
export function switchToSibling(
  conversation: Conversation,
  target_id: string,
  new_sibling_id: string,
): Conversation {
  const target = conversation.nodes[target_id];
  const sibling = conversation.nodes[new_sibling_id];
  if (!target || !sibling) return conversation;
  if (target.parent_id !== sibling.parent_id) {
    throw new Error(
      `switchToSibling invariant violation: targets (${target_id}, ${new_sibling_id}) do not share a parent`,
    );
  }
  if (target.parent_id === null) {
    return { ...conversation, selected_root_id: new_sibling_id };
  }
  const parent = conversation.nodes[target.parent_id];
  if (!parent) return conversation;
  return {
    ...conversation,
    nodes: {
      ...conversation.nodes,
      [parent.id]: { ...parent, selected_child_id: new_sibling_id },
    },
  };
}

/**
 * Set every selected-child pointer along the chain root → target so
 * that `target` appears on the active path. Other branch points'
 * selections off the chain remain untouched. Used by the chats
 * history overlay: if a search match points at an off-branch node,
 * we re-point the path to surface it before navigating in.
 */
export function selectPathTo(
  conversation: Conversation,
  target_id: string,
): Conversation {
  const chain: string[] = [];
  let cur: string | null = target_id;
  // Walk up via parent_id pointers, collecting the chain root→leaf.
  while (cur !== null) {
    const node: MessageNode | undefined = conversation.nodes[cur];
    if (!node) return conversation;
    chain.unshift(node.id);
    cur = node.parent_id;
  }
  if (chain.length === 0) return conversation;
  // Apply selections.
  const next_nodes = { ...conversation.nodes };
  for (let i = 0; i < chain.length - 1; i++) {
    const parent = next_nodes[chain[i]];
    if (!parent) return conversation;
    next_nodes[chain[i]] = {
      ...parent,
      selected_child_id: chain[i + 1],
    };
  }
  return {
    ...conversation,
    nodes: next_nodes,
    selected_root_id: chain[0],
  };
}

// ────────────────────────────────────────────────────────────────────────────
// Node constructors — keep node shapes consistent and centralised.
// ────────────────────────────────────────────────────────────────────────────

/** Construct a fresh user-message node with empty children. */
export function makeUserNode(args: {
  id: string;
  parent_id: string | null;
  content: string;
  created_at: string;
}): UserMessageNode {
  return {
    id: args.id,
    parent_id: args.parent_id,
    role: "user",
    content: args.content,
    children: [],
    selected_child_id: null,
    created_at: args.created_at,
  };
}

/** Construct an assistant-message node from a StreamCompletion. The
 *  caller supplies `parent_user_node_id` (which is the user message
 *  this assistant response answers) — the stream hook stamps it
 *  onto the completion payload at submit time. */
export function makeAssistantNode(args: {
  id: string;
  parent_user_node_id: string;
  content: string;
  citations: AssistantMessageNode["citations"];
  tool_events: AssistantMessageNode["tool_events"];
  stop_reason: AssistantMessageNode["stop_reason"];
  loop_id: string;
  strategy: string;
  model_id: string;
  step_count: number;
  tool_call_count: number;
  input_tokens: number;
  output_tokens: number;
  started_at: string;
  completed_at: string;
  grounding_supported: number | null;
  grounding_unsupported: number | null;
  grounding_uncertain: number | null;
  grounding_unverifiable: number | null;
  // ADR 0026 — true while deferred/incremental grounding is still running.
  grounding_pending?: boolean;
}): AssistantMessageNode {
  return {
    id: args.id,
    parent_id: args.parent_user_node_id,
    role: "assistant",
    content: args.content,
    children: [],
    selected_child_id: null,
    created_at: args.completed_at,
    citations: args.citations,
    tool_events: args.tool_events,
    stop_reason: args.stop_reason,
    loop_id: args.loop_id,
    strategy: args.strategy,
    model_id: args.model_id,
    step_count: args.step_count,
    tool_call_count: args.tool_call_count,
    input_tokens: args.input_tokens,
    output_tokens: args.output_tokens,
    started_at: args.started_at,
    completed_at: args.completed_at,
    grounding_supported: args.grounding_supported,
    grounding_unsupported: args.grounding_unsupported,
    grounding_uncertain: args.grounding_uncertain,
    grounding_unverifiable: args.grounding_unverifiable,
    grounding_pending: args.grounding_pending ?? false,
  };
}

/** ADR 0026 — patch the grounding verdicts onto a SETTLED assistant node.
 *
 *  In deferred/incremental modes the `answer` event archives the node before
 *  the judge has run; the late `grounding.completed` / `grounding.partial`
 *  event then patches the verdicts in place. We CANNOT re-`appendChildOf`
 *  (it throws on a duplicate id, by design), so this mutates the existing
 *  node: it replaces the content with the annotated text (chips), stamps the
 *  four counts, and clears `grounding_pending`. A no-op (same object) if the
 *  node is missing or isn't an assistant node, so a stale patch can't crash.
 *
 *  `ran === false` (judge failed) keeps the raw content + null counts and just
 *  clears the pending indicator — an honest "couldn't verify," never a hang. */
export function updateAssistantGrounding(
  conversation: Conversation,
  node_id: string,
  patch: {
    ran: boolean;
    content: string;
    grounding_supported: number | null;
    grounding_unsupported: number | null;
    grounding_uncertain: number | null;
    grounding_unverifiable: number | null;
  },
): Conversation {
  const node = conversation.nodes[node_id];
  if (!node || node.role !== "assistant") return conversation;
  const updated: AssistantMessageNode = patch.ran
    ? {
        ...node,
        content: patch.content,
        grounding_supported: patch.grounding_supported,
        grounding_unsupported: patch.grounding_unsupported,
        grounding_uncertain: patch.grounding_uncertain,
        grounding_unverifiable: patch.grounding_unverifiable,
        grounding_pending: false,
        grounding_unavailable: false,
      }
    : // Judge failed: keep raw content + null counts, clear the pending
      // spinner, and mark grounding unavailable so the UI is honest about
      // it (rather than silently showing no chip).
      { ...node, grounding_pending: false, grounding_unavailable: true };
  return { ...conversation, nodes: { ...conversation.nodes, [node_id]: updated } };
}
