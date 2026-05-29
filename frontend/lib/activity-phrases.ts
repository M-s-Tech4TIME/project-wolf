/**
 * Live activity feed (Slice 5.0c-e) — varied natural-language status
 * strings keyed by loop event. The point is to make Wolf feel like it
 * is *actively working*, not just spinning. Each event has a small pool
 * of phrasings; we pick one at random so the same line doesn't repeat
 * back-to-back on consecutive steps.
 *
 * Keep phrases short (one line) and active-voice. They sit in the
 * StreamingView's `status.message` slot, replacing whatever was there
 * for the previous event.
 */

function pick(pool: readonly string[]): string {
  return pool[Math.floor(Math.random() * pool.length)] ?? pool[0] ?? "";
}

function fmt(template: string, vars: Record<string, string | number>): string {
  return template.replace(/\{(\w+)\}/g, (_, k: string) => String(vars[k] ?? ""));
}

// ── pools ──────────────────────────────────────────────────────────────

const STEP_STARTED = [
  "Step {step}/{budget}: thinking…",
  "Step {step} of {budget} — picking the next move…",
  "Step {step}: planning what to ask next…",
  "Working through step {step}/{budget}…",
] as const;

const STEP_STARTED_NO_BUDGET = [
  "Step {step}: thinking…",
  "Step {step}: planning what to ask next…",
] as const;

const TOOL_CALL_STARTED = [
  "Searching Wazuh — `{tool}`…",
  "Calling `{tool}` against your Wazuh deployment…",
  "Reaching into Wazuh: `{tool}`…",
  "Asking Wazuh: `{tool}`…",
] as const;

const TOOL_CALL_STARTED_KNOWLEDGE = [
  "Searching the runbook + knowledge base…",
  "Looking up `{tool}` in the knowledge base…",
  "Retrieving relevant chunks via `{tool}`…",
] as const;

const TOOL_CALL_COMPLETED_OK = [
  "Got `{tool}` results ({elapsed}ms) — reading them…",
  "Returned from `{tool}` in {elapsed}ms — parsing the response…",
  "`{tool}` came back ({elapsed}ms). Folding the result into my answer…",
] as const;

const TOOL_CALL_COMPLETED_FAIL = [
  "`{tool}` failed: {error}",
  "Call to `{tool}` errored out: {error}",
] as const;

const MODEL_CALL_COMPLETED_TOOLS = [
  "Model wants to call {n} tool{s}. Dispatching…",
  "Model asked for {n} tool call{s}. Running…",
] as const;

const MODEL_CALL_COMPLETED_ANSWER = [
  "Model is drafting the answer…",
  "Model is composing the response…",
  "Model is writing — letting it stream through…",
] as const;

const MODEL_CALL_FAILED = [
  "Model call failed: {detail}",
] as const;

const GROUNDING_STARTED = [
  "Asking the grounding judge to check each claim…",
  "Validating claims against the evidence…",
  "Running the grounding judge over the answer…",
  "Cross-checking every claim against tool results + knowledge…",
] as const;

const GROUNDING_COMPLETED = [
  "Grounded the answer: {sup}✓ {uncertain}⚠ {unsup}✗",
  "Judge verdict — {sup} Verified · {uncertain} Uncertain · {unsup} Not Verified",
] as const;

const ANSWER_DONE = [
  "Done.",
  "Answer ready.",
] as const;

// ── public helper ──────────────────────────────────────────────────────

export type ActivityVars = {
  step?: number;
  budget?: number;
  tool?: string;
  elapsed?: number;
  error?: string;
  n?: number;
  s?: string; // "s" or "" for pluralisation
  detail?: string;
  sup?: number;
  uncertain?: number;
  unsup?: number;
};

export function phraseFor(
  eventType:
    | "step.started"
    | "tool.call.started"
    | "tool.call.completed.ok"
    | "tool.call.completed.fail"
    | "model.call.completed.tools"
    | "model.call.completed.answer"
    | "model.call.failed"
    | "grounding.started"
    | "grounding.completed"
    | "answer",
  vars: ActivityVars,
): string {
  switch (eventType) {
    case "step.started": {
      const pool =
        vars.budget && vars.budget > 0 ? STEP_STARTED : STEP_STARTED_NO_BUDGET;
      return fmt(pick(pool), { step: (vars.step ?? 0) + 1, budget: vars.budget ?? 0 });
    }
    case "tool.call.started": {
      const isKnowledge =
        (vars.tool ?? "").includes("runbook") ||
        (vars.tool ?? "").includes("knowledge");
      const pool = isKnowledge ? TOOL_CALL_STARTED_KNOWLEDGE : TOOL_CALL_STARTED;
      return fmt(pick(pool), { tool: vars.tool ?? "tool" });
    }
    case "tool.call.completed.ok":
      return fmt(pick(TOOL_CALL_COMPLETED_OK), {
        tool: vars.tool ?? "tool",
        elapsed: vars.elapsed ?? 0,
      });
    case "tool.call.completed.fail":
      return fmt(pick(TOOL_CALL_COMPLETED_FAIL), {
        tool: vars.tool ?? "tool",
        error: vars.error ?? "unknown",
      });
    case "model.call.completed.tools":
      return fmt(pick(MODEL_CALL_COMPLETED_TOOLS), {
        n: vars.n ?? 0,
        s: (vars.n ?? 0) === 1 ? "" : "s",
      });
    case "model.call.completed.answer":
      return pick(MODEL_CALL_COMPLETED_ANSWER);
    case "model.call.failed":
      return fmt(pick(MODEL_CALL_FAILED), { detail: vars.detail ?? "unknown" });
    case "grounding.started":
      return pick(GROUNDING_STARTED);
    case "grounding.completed":
      return fmt(pick(GROUNDING_COMPLETED), {
        sup: vars.sup ?? 0,
        uncertain: vars.uncertain ?? 0,
        unsup: vars.unsup ?? 0,
      });
    case "answer":
      return pick(ANSWER_DONE);
  }
}
