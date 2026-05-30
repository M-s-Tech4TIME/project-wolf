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

  const hasQuery = query.trim().length > 0;
  const hasMatches = matchCount > 0;
  const human =
    hasQuery && hasMatches
      ? `${activeIndex + 1} / ${matchCount}`
      : hasQuery
        ? "no matches"
        : "";

  return (
    <div className="flex shrink-0 items-center gap-2 border-b border-border bg-card/90 px-4 py-2 backdrop-blur animate-in slide-in-from-top-2 duration-200">
      <div className="relative mx-auto flex w-full max-w-3xl items-center gap-2">
        <Search className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
        <Input
          ref={inputRef}
          value={query}
          onChange={(e) => onQueryChange(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Find in this conversation…"
          aria-label="Search within this conversation"
          className="h-8 pl-9 pr-24 text-sm"
        />
        <div className="pointer-events-none absolute right-2 top-1/2 -translate-y-1/2 text-[10px] text-muted-foreground">
          {human}
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
      </div>
    </div>
  );
}
