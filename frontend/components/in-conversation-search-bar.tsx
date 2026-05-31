"use client";

import { ChevronDown, ChevronUp, Search, X } from "lucide-react";
import { useEffect, useRef, type KeyboardEvent } from "react";

import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";

type Props = {
  open: boolean;
  query: string;
  matchCount: number;
  /** 0-based index into the matching exchanges. -1 when no matches. */
  activeIndex: number;
  onQueryChange: (q: string) => void;
  onNext: () => void;
  onPrev: () => void;
  onClose: () => void;
};

/**
 * Slice 5.0c-i.2: in-conversation Find. Mounts just below the top bar
 * when `open`, exposes:
 *   - text input (auto-focuses on open)
 *   - "M / N" match counter
 *   - prev / next buttons that cycle through matching messages
 *   - close (X / Esc)
 *
 * The actual matching + scroll-into-view lives in MessageThread. This
 * component is purely the input surface; chat-shell owns the state so
 * it can be persisted across re-renders of the message thread.
 *
 * Keyboard:
 *   - Enter  → next match
 *   - Shift+Enter → previous match
 *   - Esc    → close
 */
export function InConversationSearchBar({
  open,
  query,
  matchCount,
  activeIndex,
  onQueryChange,
  onNext,
  onPrev,
  onClose,
}: Props) {
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (open) requestAnimationFrame(() => inputRef.current?.select());
  }, [open]);

  function handleKeyDown(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === "Enter") {
      e.preventDefault();
      if (e.shiftKey) onPrev();
      else onNext();
    } else if (e.key === "Escape") {
      e.preventDefault();
      onClose();
    }
  }

  if (!open) return null;

  const trimmedLen = query.trim().length;
  const hasQuery = trimmedLen > 0;
  const tooShort = hasQuery && trimmedLen < 3;
  const hasMatches = matchCount > 0;
  // Slice 5.0c-i.5: 3-character threshold message takes priority over
  // "no matches" so the user understands WHY nothing is highlighted.
  // The "M of N matches" form only kicks in once the query is long
  // enough to drive a real search.
  const counter = !hasQuery
    ? null
    : tooShort
      ? "type 3+ characters"
      : hasMatches
        ? `${activeIndex + 1} of ${matchCount} matches`
        : "no matches";

  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-border bg-card/90 px-4 py-2 backdrop-blur animate-in slide-in-from-top-2 duration-200">
      <div className="mx-auto flex w-full max-w-3xl items-center gap-2">
        <div className="relative flex-1">
          <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
          <Input
            ref={inputRef}
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            onKeyDown={handleKeyDown}
            placeholder="Find in this conversation…"
            aria-label="Search within this conversation"
            className="h-8 pl-9 text-sm"
          />
        </div>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={onPrev}
          disabled={!hasMatches}
          aria-label="Previous match (Shift+Enter)"
          title="Previous match (Shift+Enter)"
        >
          <ChevronUp className="h-4 w-4" />
        </Button>
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={onNext}
          disabled={!hasMatches}
          aria-label="Next match (Enter)"
          title="Next match (Enter)"
        >
          <ChevronDown className="h-4 w-4" />
        </Button>
        {/* Slice 5.0c-i.4: layout reorder per user feedback —
            counter moves to the FAR RIGHT, after the Close X, so
            the close affordance keeps its conventional position
            adjacent to the navigation buttons. */}
        <Button
          variant="ghost"
          size="sm"
          className="h-8 w-8 p-0"
          onClick={onClose}
          aria-label="Close find bar (Esc)"
          title="Close (Esc)"
        >
          <X className="h-4 w-4" />
        </Button>
        {counter ? (
          <span
            className="min-w-[6.5rem] shrink-0 text-right text-[11px] text-muted-foreground"
            aria-live="polite"
          >
            {counter}
          </span>
        ) : null}
      </div>
    </div>
  );
}
