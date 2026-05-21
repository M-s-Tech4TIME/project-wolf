"use client";

import { useState } from "react";

import { ChatComposer } from "@/components/chat-composer";
import { ChatHeader } from "@/components/chat-header";
import { ChatSidebar } from "@/components/chat-sidebar";
import { CitationsPanel } from "@/components/citations-panel";
import { MessageThread } from "@/components/message-thread";
import { useChatStream } from "@/hooks/use-chat-stream";
import type { ChatExchange } from "@/lib/types";

/**
 * The chat UI:
 *  ┌────────────────────────────────────────────────────────────┐
 *  │ Header: app + tenant switcher + user menu                   │
 *  ├──────────┬──────────────────────────────────────────────────┤
 *  │ Sidebar  │   Message thread                                  │
 *  │ history  │                                                   │
 *  │          │                                                   │
 *  │          │   Composer                                        │
 *  └──────────┴──────────────────────────────────────────────────┘
 */
export function ChatShell() {
  const stream = useChatStream();
  const [history, setHistory] = useState<ChatExchange[]>([]);
  const [activeId, setActiveId] = useState<string | null>(null);

  // When a stream completes, archive it into history.
  if (stream.exchange && stream.exchange.id !== activeId) {
    setHistory((prev) => [stream.exchange as ChatExchange, ...prev]);
    setActiveId(stream.exchange.id);
  }

  const handleSubmit = async (question: string) => {
    setActiveId(null); // new exchange about to start
    await stream.submit(question);
  };

  const activeExchange =
    history.find((h) => h.id === activeId) ?? stream.exchange ?? null;

  return (
    <div className="flex h-screen flex-col">
      <ChatHeader />
      <div className="flex flex-1 overflow-hidden">
        <ChatSidebar
          history={history}
          activeId={activeId}
          onSelect={(id) => {
            stream.reset();
            setActiveId(id);
          }}
          onNew={() => {
            stream.reset();
            setActiveId(null);
          }}
        />
        <main className="flex flex-1 flex-col overflow-hidden">
          <MessageThread
            exchange={activeExchange}
            stream={stream}
            pendingQuestion={stream.status.phase === "running" ? undefined : undefined}
          />
          <div className="border-t border-border bg-card/50 p-4">
            <ChatComposer
              onSubmit={handleSubmit}
              disabled={stream.status.phase === "running"}
            />
          </div>
        </main>
        <aside className="hidden w-80 border-l border-border bg-card/30 lg:block">
          <CitationsPanel
            citations={
              activeExchange?.citations ??
              (stream.status.phase === "running" ? stream.citations : [])
            }
            toolEvents={
              activeExchange?.tool_events ??
              (stream.status.phase === "running" ? stream.toolEvents : [])
            }
          />
        </aside>
      </div>
    </div>
  );
}
