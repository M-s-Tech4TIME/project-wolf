# Wolf — Claude-Style Markdown Generation & Rendering Mechanism

> **Purpose of this document.** This is the complete specification for giving Wolf the
> same response-formatting intelligence that Claude has: knowing *when* to emit a fenced
> code block vs inline code, organizing answers into clean orchestrated sections, and
> rendering each piece into the correct visual component — robustly, dynamically, and
> responsively. It documents both the *mechanism* (how it works) and the *implementation*
> (what to build). Claude Code should read this top to bottom before writing any code.

---

## 0. The goal and the one guardrail

**The goal:** Wolf should format responses the way Claude does — knowing when to use a
fenced code block vs inline code, organizing answers into clean sections, and rendering
each into the right component, robustly and dynamically. This feature should blend
naturally into Wolf's existing markdown response flow.

**Use your own judgment on *how*.** Do whatever it takes to achieve this target properly —
append, extend, refactor, or restructure as you see fit. You are not restricted to a
narrow set of edits; pick the cleanest, most robust implementation.

**The one guardrail:** do not compromise or break any existing Wolf feature. The RAG,
the Ollama/OpenRouter model handling, storage, and current rendering must all still work
correctly after your changes. Improving or reorganizing them in service of this goal is
fine; degrading or breaking them is not.

Model choice is irrelevant to this feature. It must work identically whether Wolf is
calling a local Ollama model (Qwen3-8B, Qwen3-4B) or an OpenRouter model. The mechanism
operates on the **markdown the model emits** and on **how the frontend renders it** — not
on which model produced it.

---

## 1. The mechanism: the two layers that produce this behavior

This is framed as observable behavior to reproduce, not a claim about any model's internal
architecture (see the companion document `HOW_CLAUDE_ORGANIZES_RESPONSES.md`, Section 8).
The behavior comes from two independent layers working together. Wolf needs both. If only
one is present, the feature appears broken.

```
  LAYER 1 — GENERATION                 LAYER 2 — RENDERING
  (what the model writes)              (how the browser shows it)
  ┌─────────────────────────┐          ┌──────────────────────────┐
  │ Model emits Markdown    │          │ Markdown parser reads the │
  │ with correct delimiters:│  ──────▶ │ delimiters and routes each│
  │  ``` fenced blocks ```  │   text   │ token to a component:     │
  │  `inline code`          │  stream  │  fence  → code box        │
  │  plain prose            │          │  inline → highlight pill  │
  │  # headers, lists       │          │  prose  → paragraph       │
  └─────────────────────────┘          └──────────────────────────┘
```

### 1.1 Layer 1 — Generation (the decision of *what* to write)

When Claude is about to output something, it classifies the content **semantically before
generating the first character**, then commits to a markdown format for that unit. The
classification buckets:

| Content being produced | Markdown form chosen | Why |
|---|---|---|
| A terminal command to run | **fenced block** ` ```bash ` | user will copy-paste it; needs to be isolated, scrollable, one command per line |
| Code / config / JSON / YAML / log output | **fenced block** with language tag | structured, multi-line, benefits from syntax highlighting |
| A single flag, path, ID, port, placeholder, status, date, number, short quote, or function name mentioned mid-sentence | **inline code** `like this` | it's a reference *within* prose, not a standalone block |
| Narrative explanation, reasoning, description | **plain text** | it's prose |
| A procedure with multiple steps | **numbered list**, each step's prose outside the block, each step's command(s) in their own fenced block | mirrors how a human reads instructions |

**Key properties of the generation mechanism:**

1. **The delimiters are part of the token stream.** Claude does not generate raw text and
   then wrap it afterward. It emits ` ```bash ` as the literal first tokens of that unit,
   then the command lines, then the closing ` ``` `. There is no separate post-processing
   pass. Wolf inherits this for free — the model writes the backticks itself, *provided the
   system prompt instructs it to*.

2. **Classification is semantic, not length-based.** A 3-word command still goes in a fence
   if it's something to run; a 10-word product name stays as prose. The deciding question
   is *"is this a thing the user acts on / copies / reads as a unit?"* not *"is this long?"*

3. **One command per line inside a block.** Claude never concatenates unrelated commands
   into a run-on line. Related commands are chained with `&&` or split across newlines.
   (Wolf's current bug is exactly the opposite — it mashes a whole install sequence into a
   single inline run.)

4. **Coexistence.** Fenced blocks and inline code appear in the *same* response naturally:
   prose explains, an inline `<MANAGER_IP>` highlights a placeholder, and a fenced block
   holds the command. This is what makes the output feel dynamic and robust.

5. **Commit and don't drift.** Once a unit is classified, Claude does not switch formats
   mid-output (e.g. starting a command inline then continuing it in a fence). Consistency
   is enforced by the worked example in the prompt — models imitate a demonstrated pattern
   far more reliably than they follow abstract rules.

**How to install this layer in Wolf:** add a formatting-rules block (Section 3) to Wolf's
system prompt so the model — any model, Ollama or OpenRouter — emits correct markdown.
This is the higher-priority fix, because Wolf's screenshots show the model emitting single
backticks around multi-line commands; the renderer never even receives a fence to style.

### 1.2 Layer 2 — Rendering (the decision of *how* to show it)

Once the model emits correct markdown, a parser walks the text and routes each element to
a component:

- A **fenced block** (` ```lang ... ``` `) → a code-box component: language label, copy
  button, syntax highlighting, and **horizontal scroll** (`overflowX: auto` +
  `whiteSpace: pre`) so long lines scroll inside the box instead of wrapping or breaking
  the layout. This horizontal-scroll behavior is the "scrolling feature" seen in Claude's
  UI.
- **Inline code** (`` `x` ``) → a small highlight pill (`<code>` with padded, rounded
  background).
- **Headers / lists / bold / paragraphs** → their normal HTML elements.

The renderer must distinguish **inline vs block** code. In `react-markdown`, the `code`
component callback receives an `inline` boolean — that single flag is the entire decision
point. `inline === true` → pill. `inline === false` → code box.

**How to install this layer in Wolf:** Wolf likely already uses `react-markdown`. Wire in
a `code` component that distinguishes inline vs fenced (Section 4 shows how). If Wolf
renders assistant text some other way, integrate a proper markdown renderer in whatever
way produces the cleanest result — the requirement is that fenced blocks and inline code
render distinctly, not the specific integration path.

### 1.3 Why both layers are mandatory

- Fix only Layer 1 → model emits correct fences, but a weak renderer styles them poorly.
- Fix only Layer 2 → renderer is ready, but the model still emits single backticks around
  multi-line commands (Wolf's current bug), so no fence ever reaches the renderer.
- Wolf's screenshots prove the **primary** break is Layer 1 (generation). Layer 2 is the
  secondary polish that produces the Claude look. Do both.

---

## 2. Architecture overview for Wolf

```
User question
     │
     ▼
[ Existing RAG retrieval ]
     │
     ▼
[ Build message payload ]
     │   └── system prompt  ◀── add formatting-rules block here (Layer 1)
     ▼
[ LLM call: Ollama (Qwen3-8B/4B) OR OpenRouter ]
     │
     ▼
Markdown string in assistant message  ← now contains correct ``` fences + `inline`
     │
     ▼
[ Assistant message renderer ]  ◀── wire in distinct fenced/inline rendering (Layer 2)
     │
     ▼
Rendered UI: prose + code boxes (scrollable) + inline pills, coexisting
```

The two functional touch-points are the **system prompt** (Layer 1) and the **renderer**
(Layer 2). The RAG, model routing, Ollama/OpenRouter selection, and storage are not the
target of this feature — implement Layers 1 and 2 in whatever way is cleanest, as long as
those existing capabilities keep working correctly.

---

## 3. Layer 1 implementation — system prompt block

Add the following formatting-rules block to Wolf's system prompt (placing it where it
reads cleanly alongside the existing prompt content). Use the text as-is:

````text
## Markdown formatting rules

Format every response as clean Markdown. Before emitting any content, classify what you
are about to write and route it to the correct format:

1. A command the user runs in a terminal, OR code / config / JSON / YAML / a log line /
   structured data
   → Use a FENCED code block. Always open with three backticks followed immediately by a
     language tag: ```bash, ```powershell, ```json, ```yaml, ```text, etc.
   → Put each command on its OWN line. Chain related commands with && or newlines.
     Never concatenate commands into a single run-on line.
   → Close the block with three backticks on their own line.
   → NEVER wrap a multi-line command or command sequence in single backticks. Forbidden.

2. A short in-sentence reference — a single flag, path, command name, a placeholder such
   as `<MANAGER_IP>` or `<AGENT_KEY>`, an ID like `5710`, a port like `1514/TCP`, a status
   like `Connected to manager`, a date, a number, a short quote, or a field/function name
   → Use INLINE code (single backticks). Keep it under ~8 words. No line breaks. Never put
     a full standalone command or multiple commands in inline code.

3. A multi-step procedure (installation, configuration, etc.)
   → Use a numbered list. Put the prose explanation for each step OUTSIDE the code block,
     and put that step's command(s) in their OWN fenced block directly beneath it.

4. Anything else → plain text prose. No code formatting.

Fenced blocks and inline code should coexist naturally within the same response: prose
explains, inline code highlights short references, and fenced blocks hold commands or code.
Commit to the chosen format for each unit and do not switch mid-output. When unsure between
inline and fenced, default to a fenced block.

### Worked example — imitate this structure exactly

To install the agent on Debian/Ubuntu:

1. **Add the Wazuh repository.** Import the GPG key and register the repo:

   ```bash
   curl -s https://packages.wazuh.com/key/GPG-KEY-WAZUH | \
     sudo gpg --dearmor -o /usr/share/keyrings/wazuh.gpg
   echo "deb [signed-by=/usr/share/keyrings/wazuh.gpg] \
     https://packages.wazuh.com/4.x/apt/ stable main" | \
     sudo tee /etc/apt/sources.list.d/wazuh.list
   ```

2. **Install the agent.** Replace `<MANAGER_IP>` with your manager's IP or FQDN:

   ```bash
   sudo apt-get update
   sudo WAZUH_MANAGER="<MANAGER_IP>" apt-get install wazuh-agent
   ```

3. **Start and enable the service:**

   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now wazuh-agent
   ```

Verify the agent connected by checking for `Connected to manager` in the logs.
````

> The worked example is not optional. Keep it in the prompt — models reproduce a
> demonstrated format far more reliably than they follow abstract rules. It demonstrates
> coexistence (prose + inline `<MANAGER_IP>` + fenced blocks) in one answer.

---

## 4. Layer 2 implementation — renderer

### 4.1 Dependencies (install only if not already present)

```bash
npm install react-markdown remark-gfm react-syntax-highlighter
```

### 4.2 The decision point

`react-markdown` calls the `code` component for every code token and passes an `inline`
boolean:

- `inline === true`  → render an inline highlight pill.
- `inline === false` → render a full code box (language label + copy + syntax highlight +
  horizontal scroll).

Detect language from `className` via `/language-(\w+)/`; default to `text`.

### 4.3 Reference component (add as a new file, e.g. `MarkdownMessage.jsx`)

```jsx
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { oneDark } from "react-syntax-highlighter/dist/esm/styles/prism";
import { useState } from "react";

function CodeBlock({ language, value }) {
  const [copied, setCopied] = useState(false);
  const copy = () => {
    navigator.clipboard.writeText(value);
    setCopied(true);
    setTimeout(() => setCopied(false), 1500);
  };
  return (
    <div className="wolf-codeblock">
      <div className="wolf-codeblock__bar">
        <span>{language || "text"}</span>
        <button onClick={copy}>{copied ? "Copied" : "Copy"}</button>
      </div>
      {/* overflowX: auto = the horizontal scroll seen in Claude's UI */}
      <div style={{ overflowX: "auto" }}>
        <SyntaxHighlighter
          language={language || "text"}
          style={oneDark}
          customStyle={{ margin: 0, background: "transparent", padding: 12 }}
          codeTagProps={{ style: { whiteSpace: "pre" } }} // never wrap; scroll instead
        >
          {value}
        </SyntaxHighlighter>
      </div>
    </div>
  );
}

// Use this to render assistant message content with distinct fenced/inline handling.
export function MarkdownMessage({ content }) {
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{
        code({ inline, className, children, ...props }) {
          const match = /language-(\w+)/.exec(className || "");
          const value = String(children).replace(/\n$/, "");
          if (inline) {
            return (
              <code className="wolf-inline-code" {...props}>
                {children}
              </code>
            );
          }
          return <CodeBlock language={match ? match[1] : ""} value={value} />;
        },
      }}
    >
      {content}
    </ReactMarkdown>
  );
}
```

### 4.4 Critical rendering details (the "robust / dynamic / responsive" part)

- **Horizontal scroll, never wrap:** `overflowX: auto` on the wrapper + `whiteSpace: pre`
  on the code. Long lines scroll inside the box; the layout never breaks. This is the exact
  behavior circled in the reference screenshots.
- **Language label** comes from the fence tag (` ```bash ` → `bash`).
- **Copy button** is per-block and copies the raw command text.
- **Inline pills** are small, padded, rounded `<code>` elements that flow inside prose.
- **Coexistence** falls out automatically: the parser routes each token to the right
  component, so prose + pills + boxes render together in one message.

Styling/colors are intentionally left to Wolf's own CSS (classes `wolf-codeblock`,
`wolf-codeblock__bar`, `wolf-inline-code`). The **mechanism** is what matters; theme it to
match Wolf.

---

## 5. Implementation steps for Claude Code (in order)

1. **Diagnose first.** Locate and print:
   - where the message payload / system prompt is built and sent to Ollama and OpenRouter
     (note whether both paths share one system prompt; if not, both need the block);
   - the component that renders assistant message content (is it already `react-markdown`,
     or raw HTML / plain text?).
   Report findings so the implementation fits Wolf's actual structure.

2. **Layer 1.** Add the Section 3 formatting-rules block to the system prompt so it takes
   effect for both the Ollama and OpenRouter paths. Integrate it cleanly with the existing
   prompt.

3. **Layer 2.** Implement the renderer per Section 4 so fenced blocks and inline code render
   distinctly. Use your judgment on the cleanest integration with Wolf's current rendering.

4. **Verify** (Section 6).

5. **Report** what you found, what you changed and where, and confirm the acceptance tests
   pass — including that existing Wolf capabilities still work.

---

## 6. Acceptance tests

Run the query **"How do I install the Wazuh agent? Give me a step-by-step guide."** against
both an Ollama model and an OpenRouter model. Confirm in the rendered UI:

- [ ] Multi-line commands render as **fenced, scrollable code boxes**, not inline run-ons.
- [ ] Each command is on its **own line** inside the block (no concatenation).
- [ ] Short references (`<MANAGER_IP>`, `1514/TCP`, `5710`, `Connected to manager`) render
      as **inline pills**.
- [ ] Fenced blocks and inline code **coexist** in the same answer.
- [ ] Long lines **scroll horizontally** inside the box; layout does not break.
- [ ] Language label + copy button appear on each block.
- [ ] Behavior is identical regardless of model (Ollama vs OpenRouter).
- [ ] **Existing Wolf capabilities still work** — RAG, model routing, storage, and prior
      rendering all function correctly after the change.

---

## 7. Domain vocabulary (so prompts stay self-contained)

- **Generation layer:** the model emitting correct markdown delimiters in its token stream.
- **Rendering layer:** the frontend parsing markdown and routing tokens to components.
- **Fenced block:** triple-backtick code block with a language tag.
- **Inline code:** single-backtick short reference inside prose.
- **Coexistence:** prose + inline pills + fenced boxes in one response.
- **Horizontal scroll:** `overflowX: auto` + `whiteSpace: pre` so long lines scroll, not wrap.
- **The guardrail:** achieve the goal in whatever way works best, without compromising or
  breaking any existing Wolf capability.
