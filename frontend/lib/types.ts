// TypeScript types mirroring the Wolf orchestrator's Pydantic schemas.
// Keep in sync with services/orchestrator/app/api/{auth,chat}.py and
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
};

/** A conversation thread on the client — many exchanges sharing context. */
export type Conversation = {
  id: string;
  title: string;
  exchanges: ChatExchange[];
  created_at: string;
  updated_at: string;
};

export type ChatResponseBody = {
  answer: string;
  citations: Citation[];
  step_count: number;
  tool_call_count: number;
  input_tokens: number;
  output_tokens: number;
  stop_reason: "answer" | "budget_exhausted" | "loop_error";
  loop_id: string;
  strategy: "frontier" | "guided" | "pipeline";
  model_id: string;
};

export type LoopEventType =
  | "loop.started"
  | "step.started"
  | "model.call.completed"
  | "model.call.failed"
  | "tool.call.completed"
  | "answer";

export type LoopEvent = {
  type: LoopEventType;
  data: Record<string, unknown>;
};

// One full chat exchange, kept on the client for the sidebar history.
export type ChatExchange = {
  id: string;
  question: string;
  answer: string;
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
};

export type ToolEvent = {
  tool_name: string;
  tool_call_id: string;
  success: boolean;
  elapsed_ms: number;
  citation?: Citation;
  counts?: Record<string, number>;
  error?: string;
};
