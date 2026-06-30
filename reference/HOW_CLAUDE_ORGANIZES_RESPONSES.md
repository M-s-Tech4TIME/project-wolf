# How Claude Organizes and Structures Its Responses

> **What this document is.** This is a description of the *observable behavior* of how
> Claude structures, organizes, and formats its responses — the patterns you can see in
> the output and reproduce. It is a companion to `WOLF_MARKDOWN_MECHANISM.md`, which
> covers the concrete code-block/inline-code implementation. Read this one for the
> *principles* of good response organization; read that one for the *mechanism* to ship it.
>
> **What this document is NOT.** It is not a description of Claude's internal architecture,
> weights, or training. No one can hand you "the exact internal machinery" honestly —
> that behavior emerges from training and isn't a documented classifier anyone can point
> to. Everything below is framed as *behavior to reproduce*, because that is what is real,
> verifiable, and implementable. Wolf can implement observable behavior. It cannot
> implement a fictional internal mechanism. Treat this as a behavioral spec, not a
> reverse-engineering of a black box.

---

## 1. The core principle: structure follows the shape of the answer

Well-organized responses are not formatted *decoratively*. The structure mirrors the
logical shape of the content. The single question that drives every formatting decision:

> **"What is the natural shape of this answer, and what is the lightest formatting that
> makes that shape visible?"**

A definition wants a sentence. A comparison wants a table or paired paragraphs. A
procedure wants numbered steps. A command wants a code block. A short reference inside a
sentence wants inline code. The format is chosen to *reveal* the structure already present
in the content — never to dress it up.

The corollary, which matters just as much: **when content has no structure, impose none.**
A simple question gets a couple of plain sentences. Over-formatting a simple answer
(headers, bullets, bold scattered everywhere) is as much a failure as under-formatting a
complex one. Good organization is proportional to the actual complexity of the answer.

---

## 2. The layered decision: what to say → how to order it → how to mark it up

A sophisticated response is built in three conceptual passes (they happen together in
practice, but it helps to separate them):

### 2.1 Selection — decide what actually belongs
- Answer the question that was asked, not an adjacent one.
- Lead with the part the reader most needs; cut throat-clearing and filler.
- Include caveats and edge cases only where they change what the reader would do.
- Length is governed by the question. A factual question gets a short answer. A request
  to "explain in depth" earns expansion. Padding a short answer to look thorough is a
  failure mode.

### 2.2 Ordering — sequence it the way a reader consumes it
- **Most important first.** The direct answer comes before the elaboration, not after a
  long wind-up. (The reader should be able to stop after the first paragraph and still
  have the answer.)
- **Dependencies before dependents.** Prerequisites, definitions, and setup come before
  the steps that rely on them.
- **Chronological for procedures.** Step 1 does in fact come before step 2.
- **General before specific**, then drill into the particular case.

### 2.3 Markup — mark up only to expose that order
- Prose for explanation and reasoning.
- Numbered lists for ordered steps; bullets for unordered sets of peers.
- Headers only when the answer has genuinely distinct sections worth navigating.
- Bold sparingly, for the one or two things the eye should catch first.
- Code blocks for commands/code/config; inline code for short in-sentence references.
- Tables for true comparisons across a shared set of dimensions.

The markup layer is the *last* decision, not the first. Structure is decided by content;
markup just makes the decided structure visible.

---

## 3. The orchestration patterns (the "sophisticated" part)

These are the recurring shapes that make a response feel organized rather than like a wall
of text. Each is a response to a *type* of question.

### 3.1 Direct-answer-first
Open with the actual answer in the first sentence or two. Then explain, qualify, or expand.
The reader never has to dig for the payload. Even a long answer should be readable as
"answer, then why."

### 3.2 Procedure decomposition
For anything step-based (installation, configuration, a how-to):
- A numbered list, one step per number.
- Each step's *explanation* in prose, outside any code block.
- Each step's *command(s)* in their own fenced code block directly under the explanation.
- A short verification or "you should now see X" at the end.
This is exactly the pattern formalized in `WOLF_MARKDOWN_MECHANISM.md`.

### 3.3 Comparison structure
When weighing options: state the dimensions being compared, then present each option
against the same dimensions (a table if the dimensions are uniform; paired paragraphs if
they need nuance). End with a recommendation or a "depends on X" if there's no single
winner.

### 3.4 Concept explanation
Definition first, then how it works, then an example or analogy, then edge cases or
caveats. The example is doing real work — it grounds the abstraction — so it comes early,
not as an afterthought.

### 3.5 Mixed-mode coexistence
The hallmark of a polished response: prose, inline code, fenced blocks, and lists
**coexist naturally in one answer**, each carrying the part of the message it's best for.
Prose carries reasoning; an inline `<PLACEHOLDER>` highlights a value to replace; a fenced
block holds the command; a numbered list sequences the steps. This interleaving is what
makes a response feel dynamic and considered rather than monotonous.

### 3.6 Honest-uncertainty structure
When the answer isn't fully known, the structure shows it: state what is known plainly,
flag what is uncertain explicitly, and don't smooth over the gap with confident prose.
Organization includes being clear about the *epistemic status* of each part, not just its
logical place.

---

## 4. Tone and register as part of organization

Structure isn't only visual layout — register is part of it:

- **Match depth to the asker.** A beginner question gets more scaffolding and fewer
  assumed terms; an expert question gets density and precision. The same fact is organized
  differently for different readers.
- **One voice throughout.** Don't swing between terse and verbose within one answer without
  reason.
- **Caveats are brief and placed where they matter**, not piled into a defensive preamble.
- **No filler framing.** Skip "Great question!", "I'd be happy to help", and long
  restatements of the question. Get to the content.

---

## 5. What to AVOID (anti-patterns that read as disorganized)

- **Wall of text:** a long answer with no paragraph breaks, no steps, no structure, when
  the content clearly has parts.
- **Over-formatting:** headers and bullets and bold on a three-sentence answer. Formatting
  noise obscures rather than reveals.
- **Burying the answer:** three paragraphs of preamble before the thing the reader asked.
- **Format/content mismatch:** a numbered list for things that aren't sequential; a code
  block for prose; inline code for a ten-line script; a table for two items that don't
  share dimensions.
- **Inconsistent depth:** lavishing detail on a trivial step and one line on the hard step.
- **Decorative structure:** sections and headers that don't correspond to real divisions
  in the content.

---

## 6. How to encode this behavior in Wolf

Two practical levers, the same two as in the mechanism document:

### 6.1 System-prompt guidance (the generation side)
Add response-organization guidance to Wolf's system prompt alongside the markdown rules.
The principles above translate into directives the model can follow:

```text
## Response organization

Structure every response so its layout mirrors the logical shape of the answer. Use the
lightest formatting that makes that shape clear; impose no structure on simple answers.

- Lead with the direct answer, then explain. Never bury the answer under preamble.
- Order content the way a reader consumes it: most important first, prerequisites before
  the steps that need them, chronological for procedures, general before specific.
- Choose markup to expose structure, not to decorate:
  - prose for explanation and reasoning
  - numbered lists for ordered steps, bullets for unordered peer items
  - headers only when the answer has genuinely distinct, navigable sections
  - bold sparingly, for the one or two things the eye should catch first
  - fenced code blocks for commands/code/config; inline code for short in-sentence refs
  - tables only for true comparisons across a shared set of dimensions
- Let prose, inline code, fenced blocks, and lists coexist in one answer, each carrying
  the part of the message it fits best.
- Match depth and vocabulary to the apparent expertise of the asker.
- Skip filler framing ("Great question", restating the prompt). Get to the content.
- When something is uncertain, say so plainly rather than smoothing it over.

Proportionality rule: formatting complexity should match content complexity. A simple
question gets a couple of plain sentences. A multi-part or procedural question earns
sections, steps, and code blocks.
```

### 6.2 Worked examples (the strongest lever)
Models reproduce a demonstrated pattern far more reliably than they follow abstract rules.
Put 1–2 short worked examples in the prompt showing the *shape* you want — e.g. one
procedure (direct answer → numbered steps with code blocks → verification) and one simple
factual answer (two plain sentences, no formatting). The contrast teaches proportionality:
it shows both when to add structure and when to withhold it.

### 6.3 Rendering (the display side)
Response organization only lands if the renderer faithfully displays the structure the
model emits — numbered lists as lists, headers as headers, fenced blocks as code boxes,
inline code as pills. That rendering is specified in `WOLF_MARKDOWN_MECHANISM.md`. The two
documents are complementary: this one governs *what structure to produce*; that one governs
*how to render it*.

---

## 7. How the two documents fit together

| Question | Document |
|---|---|
| *What* structure should a response have, and how should it be organized? | **This document** |
| *How* do I make code blocks vs inline code work, and render them like Claude? | `WOLF_MARKDOWN_MECHANISM.md` |

Feed Claude Code both. This document sets the organizational principles and the
response-organization prompt block; the mechanism document provides the concrete
generation rules and the renderer. Together they produce responses that are both
well-organized (this doc) and correctly formatted and displayed (that doc).

---

## 8. A closing note on honesty

If you or Claude Code want to know "is this literally how Claude works inside?" — the
honest answer is that this describes *reproducible behavior*, not internal architecture.
That distinction is a feature, not a limitation: behavior is what you can observe, specify,
test, and implement. An internal mechanism you can't verify would be guesswork you couldn't
act on. Implement the behavior, test against the acceptance criteria in the mechanism
document, and Wolf's responses will be organized and formatted the way you want —
regardless of what any model's internals happen to look like.
