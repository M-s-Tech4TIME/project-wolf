// TypeScript types mirroring wolf-server's Pydantic schemas.
// Keep in sync with services/server/wolf_server/api/{auth,chat}.py and
// packages/schema/wolf_schema/*.

export type LoginRequest = {
  email: string;
  password: string;
};

/** One membership row in login's needs-org-selection / auto-select
 *  shapes (Phase 6.5-c-ii, ADR 0018 §login UX). */
export type MembershipInfo = {
  organization_id: string;
  organization_name: string;
  role: string;
};

/** Three-shape login response (ADR 0018): exactly one of
 *  `is_superuser`, `auto_selected_organization`, or
 *  `needs_org_selection` applies. The session cookie is authentication
 *  only — the org arrives per request via X-Organization-Id. */
export type LoginResponse = {
  user_id: string;
  email: string;
  display_name: string;
  is_superuser: boolean;
  redirect: string | null;
  auto_selected_organization: MembershipInfo | null;
  needs_org_selection: boolean;
  memberships: MembershipInfo[] | null;
  /** Phase 6.5-h: route an unverified user straight to /verify. */
  verification_status: string;
};

export type MeResponse = {
  user_id: string;
  email: string;
  display_name: string;
  /** Reflects the X-Organization-Id header when sent (per-tab org);
   *  null for org-less sessions (Superuser, pre-selection). */
  organization_id: string | null;
  role: string;
  /** Phase 6.5-h: "unverified" / "verified". An unverified non-superuser
   *  is routed to the paste-your-invite-link screen. */
  verification_status: string;
};

export type OrganizationMembership = {
  id: string;
  slug: string;
  name: string;
  role: string;
};

// ── Superuser install-admin (Phase 6.5-d) ──────────────────────────────────
// Mirror services/server/wolf_server/api/{organizations,superuser}.py.

/** An organization as the Superuser sees it (install-scoped CRUD). */
export type Organization = {
  id: string;
  name: string;
  slug: string;
  is_active: boolean;
  created_at: string;
};

/** Create payload — slug is the immutable isolation key (backend pattern
 *  ^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$); name is the only editable field. */
export type OrganizationCreate = {
  name: string;
  slug: string;
};

export type OrganizationUpdate = {
  name: string;
};

/** Break-glass: seed the first Admin into an org with zero Admins. */
export type RecoveryAdminRequest = {
  email: string;
  display_name: string;
};

export type RecoveryAdminResponse = {
  organization_id: string;
  user_id: string;
  email: string;
  role: string;
  /** Present only when a brand-new user account was created; shown once. */
  new_password: string | null;
};

/** One row of the install-wide audit view (organization_* null for
 *  install-level events). */
export type InstallAuditEvent = {
  id: string;
  event_type: string;
  event_data: Record<string, unknown> | null;
  organization_id: string | null;
  organization_name: string | null;
  user_id: string | null;
  source_ip: string | null;
  related_event_id: string | null;
  created_at: string;
};

export type InstallAuditPage = {
  events: InstallAuditEvent[];
  limit: number;
  offset: number;
};

// ── Install-level Wazuh ecosystem topology (Phase 6.6-a/b) ──────────────────
// Mirror services/server/wolf_server/api/wazuh_topology.py + wazuh/topology.py.

// One addressable Wazuh component; `name` is an optional friendly label.
export type WazuhNode = { url: string; name?: string | null };

export type WazuhSingleTopology = {
  kind: "single";
  indexer_url: string;
  manager_url: string;
  dashboard_url: string;
};

export type WazuhDistributedTopology = {
  kind: "distributed";
  indexer_nodes: WazuhNode[];
  manager_master: WazuhNode;
  manager_workers: WazuhNode[];
  dashboards: WazuhNode[];
};

export type WazuhTopologyShape = WazuhSingleTopology | WazuhDistributedTopology;

export type WazuhTopologyResponse = {
  configured: boolean;
  kind: "single" | "distributed" | null;
  topology: WazuhTopologyShape | null;
  indexer_admin_user: string | null;
  manager_api_user: string | null;
  verify_tls: boolean | null;
  validated_at: string | null;
  updated_at: string | null;
};

export type WazuhTopologyUpdate = {
  topology: WazuhTopologyShape;
  indexer_admin_user: string;
  // null/omitted keeps the stored password; required on first save.
  indexer_admin_password: string | null;
  manager_api_user: string;
  manager_api_password: string | null;
  verify_tls: boolean;
};

export type WazuhProbeResult = {
  role: string;
  url: string;
  ok: boolean;
  detail: string;
  status_code: number | null;
};

export type WazuhTopologySaveResponse = WazuhTopologyResponse & {
  probe_results: WazuhProbeResult[];
  warnings: string[];
};

// ── Per-org Wazuh credentials (Phase 6.6-c/d) ───────────────────────────────
// Mirror services/server/wolf_server/api/wazuh_credentials.py.

export type WazuhCredentialsResponse = {
  configured: boolean;
  organization_id: string | null;
  indexer_user: string | null;
  server_api_user: string | null;
  wazuh_index_filter: string | null;
  // The agent.labels.group value(s) the indexer-query filter scopes to.
  agent_group_labels: string[] | null;
  inject_group_label_filter: boolean | null;
  validated_at: string | null;
  updated_at: string | null;
};

export type WazuhCredentialsUpdate = {
  indexer_user: string;
  // null/omitted keeps the stored password; required on first save.
  indexer_password: string | null;
  server_api_user: string;
  server_api_password: string | null;
  wazuh_index_filter: string;
  agent_group_labels: string[] | null;
  inject_group_label_filter: boolean;
};

export type WazuhCredentialsSaveResponse = WazuhCredentialsResponse & {
  probe_ok: boolean;
  probe_results: WazuhProbeResult[];
  agent_count: number | null;
  group_count: number | null;
  // Distinct groups the Server-API credential is scoped to (from RBAC policies).
  groups: string[] | null;
  // Per-index read-access verdict (one per configured index pattern).
  index_results: WazuhIndexAccess[];
  scope_detail: string | null;
  warnings: string[];
};

export type WazuhIndexAccess = {
  pattern: string;
  ok: boolean;
  detail: string;
  status_code: number | null;
};

export type WazuhCredentialHistoryEntry = {
  id: string;
  created_at: string;
  user_id: string | null;
  probe_ok: boolean | null;
  index_filter: string | null;
  agent_count: number | null;
  group_count: number | null;
};

// ── Per-org user management (Phase 6.5-e) ──────────────────────────────────
// Mirror services/server/wolf_server/api/org_management.py. All org-scoped
// (the active-org header rides on every call).

/** Roles an org Admin may assign (backend ASSIGNABLE_ROLES). "superuser" is
 *  deliberately absent — it goes through the consent-gate endpoints. */
export const ORG_ROLES = ["admin", "engineer", "responder", "analyst"] as const;
export type OrgRole = (typeof ORG_ROLES)[number];

export type Member = {
  user_id: string;
  email: string;
  display_name: string;
  role: string;
  is_active: boolean;
  member_since: string;
  /** Phase 6.5-h: "unverified" / "verified". The raw token is never
   *  exposed here — only the status + the current token's expiry. */
  verification_status: string;
  invite_token_expires_at: string | null;
};

export type MemberCreate = {
  email: string;
  display_name: string;
  role: string;
};

/** POST /organization/users response — extends Member with a one-time
 *  password (new account only) and the raw invite token, both shown once. */
export type MemberCreateResponse = Member & {
  new_password: string | null;
  invite_token: string | null;
};

/** POST /organization/users/{id}/regenerate-invite-link response — a fresh
 *  single-use invite token, returned once (the old one is invalidated). */
export type RegenerateInviteResponse = {
  invite_token: string;
  invite_token_expires_at: string;
};

export type RoleChange = {
  role: string;
};

/** POST /organization/users/{id}/password-reset response — the freshly
 *  generated password, returned once for out-of-band delivery. */
export type MemberPasswordReset = {
  user_id: string;
  email: string;
  new_password: string;
};

/** One row of the org's own audit trail (no org-name column — it's
 *  implicitly the active org). */
export type OrgAuditEvent = {
  id: string;
  event_type: string;
  event_data: Record<string, unknown> | null;
  user_id: string | null;
  source_ip: string | null;
  related_event_id: string | null;
  created_at: string;
};

export type OrgAuditPage = {
  events: OrgAuditEvent[];
  limit: number;
  offset: number;
};

// ── Superuser-membership consent gate (Phase 6.5-f) ─────────────────────────
// Mirror services/server/wolf_server/api/{superuser,org_management}.py.
// Flow: Superuser requests → Admin approves/rejects → time-limited grant →
// expiry/revoke → all org members see a transparency banner.

export type AccessRequestStatus =
  | "pending"
  | "approved"
  | "rejected"
  | "cancelled"
  // Terminal states an APPROVED grant lands in (timeline tail):
  | "revoked"
  | "expired";

/** Duration presets for the request + approve dialogs. `null` = "until
 *  revoked" (open-ended). Kept here so both UIs share one source. */
export const ACCESS_DURATION_OPTIONS: { label: string; hours: number | null }[] = [
  { label: "1 hour", hours: 1 },
  { label: "4 hours", hours: 4 },
  { label: "8 hours", hours: 8 },
  { label: "24 hours", hours: 24 },
  { label: "72 hours", hours: 72 },
  { label: "Until revoked", hours: null },
];

/** A Superuser's own access-request (Superuser-side view). */
export type SuperuserAccessRequest = {
  id: string;
  organization_id: string;
  organization_name: string;
  status: AccessRequestStatus;
  reason: string | null;
  requested_duration_hours: number | null;
  granted_expires_at: string | null;
  requested_at: string;
  decided_at: string | null;
  /** When an approved grant ended (revoked/expired); null otherwise. */
  ended_at: string | null;
  /** True when this approval is presently a live (non-expired) grant. */
  currently_active: boolean;
};

export type AccessRequestCreate = {
  reason?: string | null;
  /** null = "until revoked"; otherwise 1..720 hours. Defaults to 24h. */
  requested_duration_hours?: number | null;
};

/** An access-request as the org's Admin sees it (consent-gate inbox). */
export type OrgAccessRequest = {
  id: string;
  superuser_user_id: string;
  superuser_email: string;
  superuser_display_name: string;
  status: AccessRequestStatus;
  reason: string | null;
  requested_duration_hours: number | null;
  granted_expires_at: string | null;
  requested_at: string;
  decided_at: string | null;
  decided_by_user_id: string | null;
  /** Display name of the deciding Admin (null while pending/cancelled). */
  decided_by_display_name: string | null;
  /** When an approved grant ended (revoked/expired); null otherwise. */
  ended_at: string | null;
};

/** Approval decision: honour the requested duration, override with
 *  `duration_hours`, or grant open-ended ("until_revoked"). */
export type AccessApprove = {
  mode: "requested" | "hours" | "until_revoked";
  duration_hours?: number | null;
};

/** The org's current active Superuser grant — drives the all-member
 *  transparency banner. The endpoint returns `null` when none. */
export type SuperuserAccessGrant = {
  granted_by_display_name: string | null;
  granted_at: string;
  /** null = "until revoked" (open-ended grant). */
  expires_at: string | null;
};

export type Citation = {
  tool: string;
  query: Record<string, unknown>;
  timestamp: string;
  result_count: number | null;
  /** Web-research provenance (ADR 0032, slice 6-f.3). Set only by the
   *  web_search / web_fetch / web_crawl tools; absent/null for Wazuh and
   *  knowledge citations. `source` is the docs-first tier —
   *  "official_docs" | "official" | "official_github" | "community" —
   *  so official documentation is visually distinguished in the panel. */
  url?: string | null;
  title?: string | null;
  source?: string | null;
};

export type ConversationTurn = {
  role: "user" | "assistant";
  content: string;
};

export type ChatRequestBody = {
  question: string;
  history?: ConversationTurn[];
  /** Slice 5.0c-g: Retry-on-Wolf-response. wolf-server appends a
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
   * Why the agent loop ended. The backend emits `answer` | `loop_error`
   * (`budget_exhausted` is retired since 6-f.5 — the loop has no fixed
   * step ceiling and every forced stop synthesizes a real answer; the
   * member stays for conversations persisted before then). `interrupted`
   * is client-only, synthesised when the user clicks the Stop button
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
  // ADR 0026 — incremental mode emits one `grounding.partial` per judge batch.
  | "grounding.partial"
  | "grounding.completed"
  | "answer"
  // Terminal error the SSE endpoint emits when the run fails after the
  // response already started (so no HTTP error can be returned). The client
  // settles into an error state instead of hanging on "thinking…".
  | "error";

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
  // ADR 0026 — deferred/incremental grounding: the answer settled before the
  // verdicts. true while the judge is still running (counts null + a
  // "Verifying claims…" indicator); cleared when the verdicts patch in.
  grounding_pending?: boolean;
  // Set true when grounding was ATTEMPTED but the judge failed (ran=false) —
  // e.g. every judge provider in the chain was rate-limited. Distinguishes an
  // honest "couldn't verify" from "nothing to verify" (both leave counts null),
  // so the UI can say so instead of silently showing no chip (2026-07-01).
  grounding_unavailable?: boolean;
};

// ── Action proposals (Phase 6, ADR 0025) ──────────────────────────────────

/** The proposal lifecycle (doc 04). `pending` is the only actionable state;
 *  the rest are produced by approval/execution and shown as history. */
export type ProposalState =
  | "draft"
  | "pending"
  | "approved"
  | "executing"
  | "succeeded"
  | "failed"
  | "rejected"
  | "expired"
  | "rolled_back";

/** One capability-driven action proposal — mirrors the backend `ProposalOut`
 *  (services/server/wolf_server/api/action_proposals.py). Wolf proposes; a
 *  human with ACTION_APPROVE approves; only then does the gateway execute. */
export type ActionProposal = {
  id: string;
  action_class: string;
  /** The concrete action/command (e.g. "firewall-drop"). */
  action: string;
  /** Resolved target, e.g. `{ agent_id: "002" }`. */
  target: Record<string, unknown>;
  parameters: Record<string, unknown>;
  /** "low" | "high" today (compute_severity); kept open for future tiers. */
  severity: string;
  state: ProposalState;
  rationale: string;
  expected_effect: string;
  /** Grounding evidence, e.g. `{ alert_ids: [...] }`. */
  evidence: Record<string, unknown>;
  rollback_plan: string | null;
  requested_by: string;
  approved_by: string | null;
  approved_at: string | null;
  executed_at: string | null;
  /** Verification-read outcome (or `{ error }` on failure); null until run. */
  result: Record<string, unknown> | null;
  created_at: string;
  /** TTL — a pending proposal past this is refused as stale. */
  expires_at: string;
  /** Reversal linkage (slice 6-d, ADR 0028). On a REVERSAL row: the block it
   *  undoes. On a BLOCK row: `reversal_proposal_id` is the reversal authorised
   *  for it; `auto_unblock_at` is when a TIMED block auto-reverses. */
  reverses_proposal_id: string | null;
  reversal_proposal_id: string | null;
  auto_unblock_at: string | null;
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
  // ADR 0026 — set from the `answer` event's `grounding_pending` in
  // deferred/incremental modes; the late grounding event patches the
  // archived node's verdicts and clears this.
  grounding_pending?: boolean;
};
