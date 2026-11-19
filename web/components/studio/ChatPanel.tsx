"use client";

// Chat over the room's `live_chat` text stream. Real messages only: what
// other participants sent, plus this user's own lines once LiveKit accepted
// them (the SFU does not echo a stream back to its sender).

import { Send } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import type { ChatMessage } from "@/lib/studio/types";
import { Button, inputClass } from "./ui";

export interface ChatPanelProps {
  messages: ChatMessage[];
  /** False while the room is not connected; disables the composer. */
  connected: boolean;
  /** Display name used for outgoing messages. */
  name: string;
  onSend: (name: string, text: string) => Promise<void>;
  disabledHint?: string;
  className?: string;
}

export function ChatPanel({
  messages,
  connected,
  name,
  onSend,
  disabledHint = "Chat hoạt động khi phiên đang live.",
  className = "",
}: ChatPanelProps) {
  const [draft, setDraft] = useState("");
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = listRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [messages.length]);

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || !connected || sending) return;
    setSending(true);
    setError(null);
    try {
      await onSend(name, text);
      setDraft("");
    } catch (err) {
      setError(err instanceof Error ? err.message : "Không gửi được tin nhắn.");
    } finally {
      setSending(false);
    }
  };

  return (
    <div className={`flex min-h-0 flex-col gap-2 ${className}`}>
      <div
        ref={listRef}
        data-testid="ChatPanel.list"
        className="min-h-[8rem] flex-1 space-y-1.5 overflow-y-auto rounded-lg border border-studio-border bg-studio-card p-3 text-sm"
      >
        {messages.length === 0 ? (
          <p className="text-studio-faint">{connected ? "Chưa có tin nhắn nào." : disabledHint}</p>
        ) : (
          messages.map((m) => (
            <p key={m.id} className="break-words leading-snug">
              <span className={`font-semibold ${m.mine ? "text-amber" : "text-studio-muted"}`}>
                {m.name}
              </span>{" "}
              <span className="text-studio-fg">{m.text}</span>
            </p>
          ))
        )}
      </div>
      <form onSubmit={submit} className="flex gap-2">
        <input
          aria-label="Tin nhắn"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={connected ? "Nhập tin nhắn..." : disabledHint}
          disabled={!connected}
          maxLength={300}
          className={inputClass}
        />
        <Button
          type="submit"
          variant="primary"
          disabled={!connected || !draft.trim()}
          busy={sending}
        >
          <Send className="h-4 w-4" />
          <span className="sr-only">Gửi</span>
        </Button>
      </form>
      {error && <p className="text-xs text-danger">{error}</p>}
    </div>
  );
}
