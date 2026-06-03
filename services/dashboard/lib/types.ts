// TypeScript types mirroring wolf-server's Pydantic schemas.
// Keep in sync with services/server/wolf_server/api/{auth,chat}.py and
// packages/schema/wolf_schema/*.

export type LoginRequest = {
  email: string;
  password: string;
  tenant_id?: string | null;
};

export type LoginResponse = {
  user_id: string;
  email: string;
  display_name: string;
  tenant_id: string;
  role: string;
};

export type MeResponse = {
  user_id: string;
  email: string;
  display_name: string;
  tenant_id: string;
  role: string;
};

export type TenantMembership = {
  id: string;
  slug: string;
  name: string;
  role: string;
};

export type Citation = {
  tool: string;
  query: Record<string, unknown>;
  timestamp: string;
  result_count: number | null;
};

export type ConversationTurn = {
  role: "user" | "assistant";
  content: string;
};

export type ChatRequestBody = {
  question: string;
  history?: ConversationTurn[];
  /** Slice 5.0c-g: Retry-on-Wolf-response. The orchestrator appends a
   *  critique hint to the user message so the model knows to try to
   *  improve on its previous attempt (which sits in `history`). */
  retry_nudge?: boolean;
};

/**
 * Slice 5.0c-l (refactor 2026-06-02 to fix the cross-fork-set
 * merge bug): a conversation is a TREE of message nodes, not a list
 * of Q+A pairs. User and assistant messages are separate nodes; a
 * "branch point" is any node whose `children.length > 1`. Edit and
 * Retry both reduce to a single primitive — `fork(target)` — which
 * appends the new version to `target.parentId`'s children and
 * never to any other ancestor's. This guarantees that two forks at
 * different depths in the conversation never share a sibling set.
 *
 * See `lib/branches.ts` for the helpers (active path walk, fork,
 * switchToSibling, etc.).
 */
export type Conversation = {
  id: string;
  title: string;
  /** All message nodes in the conversation, keyed by id. The tree
   *  shape is reconstructed via `parent_id` pointers and each node's
   *  `children` array. */
  nodes: Record<string, MessageNode>;
  /** Ids of top-level user messages (`parent_id === null`). Length
   *  > 1 iff the very first user message has been edited. */
  root_children: string[];
  /** Which top-level user message is currently on the active path,
   *  or null for a brand-new empty conversation. */
  selected_root_id: string | null;
  created_at: string;
  updated_at: string;
  /**
   * Slice 5.0c-i.2: starred conversations float to the top of the
   * sidebar in their own "Starred" section. Omitted / false for the
   * default "Recents" section.
   */
  starred?: boolean;
};

export type ChatResponseBody = {
  answer: string;
  citations: Citation[];
  step_count: number;
  tool_call_count: number;
  input_tokens: number;
  output_tokens: number;
  /**
   * Why the agent loop ended. Three are emitted by the backend
   * (`answer` | `budget_exhausted` | `loop_error`); `interrupted` is
   * client-only, synthesised when the user clicks the Stop button
   * and the SSE fetch is aborted before the `answer` event arrives
   * (Slice 5.0c-k). MessageThread renders a small "Response
   * interrupted by user" footer under partial answers carrying this
   * value.
   */
  stop_reason: "answer" | "budget_exhausted" | "loop_error" | "interrupted";
  loop_id: string;
  strategy: "frontier" | "guided" | "pipeline";
  model_id: string;
  // Phase 3 Slice 2B — grounding validator verdict counts.
  // null when the validator skipped (no citations or judge failed).
  grounding_supported: number | null;
  grounding_unsupported: number | null;
  grounding_uncertain: number | null;
  grounding_unverifiable: number | null;
};

export type LoopEventType =
  | "loop.started"
  | "step.started"
  | "model.delta"
  | "model.call.completed"
  | "model.call.failed"
  | "tool.call.started"
  | "tool.call.completed"
  | "grounding.started"
  | "grounding.completed"
  | "answer";

export type LoopEvent = {
  type: LoopEventType;
  data: Record<string, unknown>;
};

/**
 * Slice 5.0c-l — node tree refactor.
 *
 * One message in the conversation tree. User and assistant messages
 * are SEPARATE nodes: editing a user message creates a sibling at
 * the user-message level, retrying an assistant message creates a
 * sibling at the assistant-message level. Each node owns its own
 * `children` array; sibling sets are LOCAL to a parent and never
 * shared across different fork points.
 *
 * `selected_child_id` records which child is on the active path so
 * we can walk root→leaf without re-deriving it each render.
 */
type BaseNode = {
  id: string;
  /** Parent node id, or null only for top-level user messages (the
   *  conversation roots). Assistant nodes ALWAYS have a non-null
   *  parent (the user message that prompted them). */
  parent_id: string | null;
  /** User question text or assistant answer text. */
  content: string;
  /** Ordered ids of this node's own version siblings in context —
   *  i.e., follow-up turns. The CRITICAL property: this array is
   *  local to this node. Fork primitives must never write into any
   *  other node's children. */
  children: string[];
  /** Which child is on the active path, or null if leaf. */
  selected_child_id: string | null;
  created_at: string;
};

export type UserMessageNode = BaseNode & { role: "user" };

export type AssistantMessageNode = BaseNode & {
  role: "assistant";
  /** Assistant nodes always have a user-message parent. */
  parent_id: string;
  // Per-run metadata captured from the SSE `answer` event (or
  // synthesised for an interrupted run).
  citations: Citation[];
  tool_events: ToolEvent[];
  stop_reason: ChatResponseBody["stop_reason"];
  loop_id: string;
  strategy: string;
  model_id: string;
  step_count: number;
  tool_call_count: number;
  input_tokens: number;
  output_tokens: number;
  started_at: string;
  completed_at: string;
  // Phase 3 Slice 2B — grounding verdict counts. null when the
  // validator didn't run (no citations, empty answer, judge failed).
  grounding_supported: number | null;
  grounding_unsupported: number | null;
  grounding_uncertain: number | null;
  grounding_unverifiable: number | null;
};

export type MessageNode = UserMessageNode | AssistantMessageNode;

export type ToolEvent = {
  tool_name: string;
  tool_call_id: string;
  success: boolean;
  elapsed_ms: number;
  citation?: Citation;
  counts?: Record<string, number>;
  error?: string;
};

/**
 * Slice 5.0c-l — the shape the stream hook produces when an SSE run
 * settles (either `answer` event or AbortError). chat-shell's
 * archive layer converts this into an `AssistantMessageNode` and
 * appends it as a child of `parent_user_node_id`. The user node
 * itself was already added to the tree synchronously at submit/save
 * time — this completion only carries the assistant half.
 */
export type StreamCompletion = {
  /** Assistant node id (loop_id from the backend, or a random
   *  fallback if the run never reached the `answer` event). */
  id: string;
  /** The user message node id this assistant response is a child
   *  of. The fork primitive guarantees this is `target.parent_id`
   *  for retries on `target`, or the freshly-created user-sibling's
   *  id for edits, or the fresh-turn user node for plain submits. */
  parent_user_node_id: string;
  content: string;
  citations: Citation[];
  tool_events: ToolEvent[];
  stop_reason: ChatResponseBody["stop_reason"];
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
};
