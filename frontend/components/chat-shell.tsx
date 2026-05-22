"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import { ChatComposer } from "@/components/chat-composer";
import { ChatHeader } from "@/components/chat-header";
import { ChatSidebar } from "@/components/chat-sidebar";
import { CitationsPanel } from "@/components/citations-panel";
import { MessageThread } from "@/components/message-thread";
import { useChatStream } from "@/hooks/use-chat-stream";
import type {
  ChatExchange,
  Conversation,
  ConversationTurn,
} from "@/lib/types";

/**
 * The chat UI:
 *  ┌────────────────────────────────────────────────────────────┐
 *  │ Header: app + tenant switcher + user menu                   │
 *  ├──────────┬──────────────────────────────────────────────────┤
 *  │ Sidebar  │   Message thread (all turns in active convo)     │
 *  │ convos   │                                                   │
 *  │          │                                                   │
 *  │          │   Composer                                        │
 *  └──────────┴──────────────────────────────────────────────────┘
 *
 * Multi-turn:
 *   - A `Conversation` is a list of exchanges sharing context.
 *   - Submitting a new question appends to the active conversation, and
 *     sends the prior user/assistant turns as `history` so the agent has
 *     context.
 *   - "New" starts a fresh conversation; clicking one in the sidebar
 *     resumes it (next question continues that thread).
 */
export function ChatShell() {
  const stream = useChatStream();
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [activeConvoId, setActiveConvoId] = useState<string | null>(null);
  const archivedRef = useRef<string | null>(null);

  // When a stream completes, archive the exchange into the active
  // conversation (or start a new one if none was active).
  useEffect(() => {
    if (!stream.exchange) return;
    if (archivedRef.current === stream.exchange.id) return;
    archivedRef.current = stream.exchange.id;

    const ex = stream.exchange;
    setConversations((prev) => {
      if (activeConvoId) {
        return prev.map((c) =>
          c.id === activeConvoId
            ? {
                ...c,
                exchanges: [...c.exchanges, ex],
                updated_at: ex.completed_at,
              }
            : c,
        );
      }
      // Start a fresh conversation seeded by this exchange.
      const newConvo: Conversation = {
        id: crypto.randomUUID(),
        title: titleFromQuestion(ex.question),
        exchanges: [ex],
        created_at: ex.started_at,
        updated_at: ex.completed_at,
      };
      setActiveConvoId(newConvo.id);
      return [newConvo, ...prev];
    });
  }, [stream.exchange, activeConvoId]);

  const activeConversation =
    conversations.find((c) => c.id === activeConvoId) ?? null;

  const handleSubmit = useCallback(
    async (question: string) => {
      const history: ConversationTurn[] = activeConversation
        ? activeConversation.exchanges.flatMap((ex) => [
            { role: "user" as const, content: ex.question },
            { role: "assistant" as const, content: ex.answer },
          ])
        : [];
      await stream.submit(question, history);
    },
    [activeConversation, stream],
  );

  const handleNew = useCallback(() => {
    stream.reset();
    setActiveConvoId(null);
    archivedRef.current = null;
  }, [stream]);

  const handleSelect = useCallback(
    (id: string) => {
      stream.reset();
      setActiveConvoId(id);
      archivedRef.current = null;
    },
    [stream],
  );

  // What to render in the message thread:
  //   - the active conversation's exchanges
  //   - plus the in-flight exchange (or "running" view) if any
  const isRunning = stream.status.phase === "running";
  const visibleExchanges: ChatExchange[] =
    activeConversation?.exchanges ?? [];
  // If a stream just finished and started a fresh conversation, the new
  // conversation already contains the exchange — no duplicate.

  // Citations panel shows the latest exchange's evidence, or the in-flight one.
  const evidenceSource: ChatExchange | null =
    isRunning
      ? null
      : visibleExchanges.length > 0
        ? visibleExchanges[visibleExchanges.length - 1]
        : null;

  return (
    <div className="flex h-screen flex-col">
      <ChatHeader />
      <div className="flex flex-1 overflow-hidden">
        <ChatSidebar
          conversations={conversations}
          activeId={activeConvoId}
          onSelect={handleSelect}
          onNew={handleNew}
        />
        <main className="flex flex-1 flex-col overflow-hidden">
          <MessageThread
            exchanges={visibleExchanges}
            stream={stream}
          />
          <div className="border-t border-border bg-card/50 p-4">
            <ChatComposer
              onSubmit={handleSubmit}
              disabled={isRunning}
            />
          </div>
        </main>
        <aside className="hidden w-80 border-l border-border bg-card/30 lg:block">
          <CitationsPanel
            citations={
              isRunning
                ? stream.citations
                : (evidenceSource?.citations ?? [])
            }
            toolEvents={
              isRunning
                ? stream.toolEvents
                : (evidenceSource?.tool_events ?? [])
            }
          />
        </aside>
      </div>
    </div>
  );
}

function titleFromQuestion(question: string): string {
  const trimmed = question.trim().replace(/\s+/g, " ");
  return trimmed.length <= 60 ? trimmed : `${trimmed.slice(0, 57)}…`;
}
