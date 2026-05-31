"use client";

import { SendHorizontal } from "lucide-react";
import { useEffect, useRef, useState, type FormEvent, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";

type Props = {
  onSubmit: (question: string) => void | Promise<void>;
  disabled?: boolean;
  /**
   * External "fill the input with this text" trigger for the hover Edit
   * and Retry actions, and for the new-chat greeting screen's quick-action
   * cards (Slice 5.0c-f). The `nonce` lets the parent re-trigger the
   * same text by bumping it. Leaving this undefined keeps the composer
   * purely uncontrolled.
   */
  draft?: { value: string; nonce: number };
};

/**
 * Slice 5.0c-i.4: cap on how tall the composer can grow before it
 * starts scrolling internally instead of pushing the layout. Equivalent
 * to ~10 lines at the current text-sm line-height (1.625 × 14px ≈
 * 22.75px; rounded up to make the breakpoint feel like 10 clean rows).
 */
const COMPOSER_MAX_HEIGHT_PX = 240;

export function ChatComposer({ onSubmit, disabled, draft }: Props) {
  const [value, setValue] = useState("");
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  // Slice 5.0c-i.7: flicker-free auto-resize. The previous (5.0c-i.4)
  // version reset `height` to "auto" before re-measuring on every
  // keystroke, which caused a brief layout flicker — even at max
  // height, the two-style-write cycle triggered enough recalc that
  // the chat thread's scroll position would jitter (user-reported).
  //
  // New strategy: only reset to "auto" when the value got SHORTER
  // (the only case where the textarea might need to shrink). When
  // the value grows, the existing scrollHeight already includes the
  // new content (the browser updates scrollHeight as content is
  // added to the textarea, regardless of the rendered height), so we
  // just set `height = min(scrollHeight, MAX)` directly. At max
  // height with content overflowing, scrollHeight stays > MAX and we
  // keep the explicit "240px" — no auto round-trip, no flicker.
  const prevLengthRef = useRef(0);
  useEffect(() => {
    const el = textareaRef.current;
    if (!el) return;
    const shrinking = value.length < prevLengthRef.current;
    prevLengthRef.current = value.length;
    if (shrinking) {
      // Only the shrinking path needs the auto-reset dance: without
      // it the textarea would never get shorter on backspace.
      el.style.height = "auto";
    }
    const desired = Math.min(el.scrollHeight, COMPOSER_MAX_HEIGHT_PX);
    el.style.height = desired + "px";
  }, [value]);

  useEffect(() => {
    if (!draft) return;
    // setState in an effect is the right shape here: an external nonce
    // signals "fill the input with this text" and we sync it in once.
    // eslint-disable-next-line react-hooks/set-state-in-effect
    setValue(draft.value);
    const el = textareaRef.current;
    if (el) {
      el.focus();
      el.setSelectionRange(draft.value.length, draft.value.length);
    }
    // Depend on the nonce rather than the value: bumping the nonce
    // re-fires the effect even if the text is identical, so an Edit/Retry
    // hover action or a quick-action card click still works on repeats.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [draft?.nonce]);

  async function send() {
    if (!value.trim() || disabled) return;
    const q = value;
    setValue("");
    await onSubmit(q);
  }

  async function handleSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    await send();
  }

  async function handleKeyDown(e: KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      await send();
    }
  }

  return (
    <form onSubmit={handleSubmit} className="mx-auto max-w-3xl">
      <div className="flex items-end gap-2 rounded-lg border border-input bg-background p-2 focus-within:ring-1 focus-within:ring-ring">
        <textarea
          ref={textareaRef}
          className="flex-1 resize-none overflow-y-auto bg-transparent px-2 py-1.5 text-sm leading-relaxed outline-none placeholder:text-muted-foreground [scrollbar-gutter:stable] [&::-webkit-scrollbar]:w-1.5 [&::-webkit-scrollbar-thumb]:rounded-full [&::-webkit-scrollbar-thumb]:bg-foreground/30 hover:[&::-webkit-scrollbar-thumb]:bg-foreground/50"
          rows={2}
          placeholder='Ask something — e.g. "why did agent web-07 trigger alert 5710 at 10:32 UTC?"'
          value={value}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={disabled}
          style={{ maxHeight: `${COMPOSER_MAX_HEIGHT_PX}px` }}
        />
        <Button
          type="submit"
          size="sm"
          disabled={disabled || !value.trim()}
          className="h-9"
        >
          <SendHorizontal className="h-4 w-4" />
        </Button>
      </div>
      <p className="mt-1.5 px-1 text-[10px] text-muted-foreground">
        Press <kbd className="rounded bg-muted px-1 py-0.5">Enter</kbd> to send,{" "}
        <kbd className="rounded bg-muted px-1 py-0.5">Shift+Enter</kbd> for newline.
      </p>
    </form>
  );
}
