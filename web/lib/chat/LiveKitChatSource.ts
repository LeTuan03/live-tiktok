// Cross-client global-chat transport built on LiveKit text streams.
//
// Wire shape (JSON, sent on topic ``GLOBAL_CHAT_TOPIC``):
//
//   { "handle": "<display name>", "body": "<message text>" }
//
// Outbound path: `room.localParticipant.sendText(payload, { topic })`
// — LiveKit fan-outs to every other participant on the same room. The
// sender is *not* echoed back to themselves (LiveKit's documented
// data-stream behaviour), so the composer's local-inject path in
// `GlobalChatStore` is the only thing that puts the user's own line
// onto their screen.
//
// Inbound path: `room.registerTextStreamHandler(topic, …)` — every
// incoming text stream on `global_chat` is parsed and dispatched to
// subscribers via the same `ChatMessageSource` interface the simulator
// uses. The view doesn't know (and shouldn't care) which source a
// message came from.
//
// `attach(room)` is idempotent: calling it with a new room detaches the
// previous one first. `ChannelRoomCoordinator` uses this to flip the
// source onto the active channel's room across swipes.

import type { Room } from "livekit-client";
import type { ChatMessageSource, GlobalChatListener, GlobalChatMessage } from "./globalChat";

export const GLOBAL_CHAT_TOPIC = "global_chat";

/** Minimal `Room`-shaped surface this source needs. Mirrors the same
 *  test-seam pattern used by `PitchEventSource` / `PersonaSwapEventSource`
 *  so a Vitest fake can stand in without pulling the full SDK. */
export interface ChatTextStreamRoom {
  registerTextStreamHandler: Room["registerTextStreamHandler"];
  unregisterTextStreamHandler: Room["unregisterTextStreamHandler"];
  localParticipant: {
    sendText: (text: string, options: { topic: string }) => Promise<unknown>;
    identity?: string;
  };
}

interface ParsedPayload {
  handle?: unknown;
  body?: unknown;
}

function parseChatPayload(text: string): { handle: string; body: string } | null {
  let raw: ParsedPayload;
  try {
    raw = JSON.parse(text) as ParsedPayload;
  } catch {
    return null;
  }
  const body = typeof raw.body === "string" ? raw.body.trim() : "";
  if (!body) return null;
  const handle = typeof raw.handle === "string" && raw.handle ? raw.handle : "viewer";
  return { handle, body };
}

export class LiveKitChatSource implements ChatMessageSource {
  private readonly listeners = new Set<GlobalChatListener>();
  private room: ChatTextStreamRoom | null = null;
  private counter = 0;
  private disposed = false;

  attach(room: ChatTextStreamRoom): void {
    if (this.disposed) return;
    if (this.room === room) return;
    if (this.room) this.detach();
    this.room = room;
    room.registerTextStreamHandler(GLOBAL_CHAT_TOPIC, async (reader, info) => {
      const text = await reader.readAll();
      const parsed = parseChatPayload(text);
      if (!parsed) return;
      this.counter += 1;
      const message: GlobalChatMessage = {
        // Identity-namespaced id keeps the React key stable across
        // hot reloads and across simultaneous messages from different
        // viewers in the same tick.
        id: `lk:${info?.identity ?? "anon"}:${Date.now()}:${this.counter}`,
        authorKind: "viewer",
        authorHandle: parsed.handle,
        text: parsed.body,
        createdAt: Date.now(),
      };
      for (const listener of this.listeners) listener(message);
    });
  }

  detach(): void {
    if (!this.room) return;
    try {
      this.room.unregisterTextStreamHandler(GLOBAL_CHAT_TOPIC);
    } catch {
      // Already unregistered (Room teardown). Swallow — detach is idempotent.
    }
    this.room = null;
  }

  /** Publish a chat message to every other participant on the attached
   *  room. No-op when not attached (e.g. between swipes). Resolves once
   *  the SFU has accepted the stream — callers can ignore the result for
   *  a fire-and-forget UX (the optimistic local echo already showed). */
  async publish(handle: string, body: string): Promise<void> {
    if (this.disposed || !this.room) return;
    const trimmed = body.trim();
    if (!trimmed) return;
    const payload = JSON.stringify({ handle, body: trimmed });
    try {
      await this.room.localParticipant.sendText(payload, {
        topic: GLOBAL_CHAT_TOPIC,
      });
    } catch {
      // Network or permission failure — the optimistic local echo is
      // already on screen, so a failed broadcast is silently lossy.
      // Surfacing the error would require a confirm/retry UX which is
      // out of scope for the demo's no-persistence model.
    }
  }

  // ChatMessageSource — `start`/`stop` are no-ops because attachment
  // is room-driven, not state-driven. The simulator owns "started";
  // we own "attached".
  start(): void {}
  stop(): void {
    this.detach();
  }

  subscribe(listener: GlobalChatListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** Test hook: synthesise an event as if it had arrived from the wire. */
  emitForTest(message: GlobalChatMessage): void {
    for (const listener of this.listeners) listener(message);
  }

  dispose(): void {
    this.disposed = true;
    this.detach();
    this.listeners.clear();
  }
}
