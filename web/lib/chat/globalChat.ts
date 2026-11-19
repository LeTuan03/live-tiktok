// Shared types + interfaces for the consumer-feed global chat.
//
// The chat is a demo surface: simulated viewer comments are scripted
// per-product, with optional user messages merged into the same stream.
// No DB persistence — the stream is ephemeral and per-session.
//
// The `ChatMessageSource` abstraction is the seam that lets the store
// stay ignorant of whether comments come from a deterministic simulator
// or, later, a real LiveKit text-stream / WS transport. The store
// depends on the interface, not the simulator (dependency inversion).

export type GlobalChatAuthorKind = "viewer" | "self";

export interface GlobalChatMessage {
  /** Stable per-render id. Simulator uses `script:<productId>:<i>`; the
   *  user-composer path uses `local:<incrementing>`. */
  id: string;
  authorKind: GlobalChatAuthorKind;
  authorHandle: string;
  text: string;
  /** Optional fragment of `text` to highlight (the "orange" inline
   *  emphasis seen on Whatnot's chat). Falls back to none. */
  highlight?: string;
  /** Epoch ms when emitted. Used as a tie-breaker for stable sort. */
  createdAt: number;
}

export type GlobalChatListener = (message: GlobalChatMessage) => void;

/**
 * Pluggable comment source. The store subscribes; sources push messages
 * via the listener. Sources own their own scheduling (timers, sockets,
 * etc.) and are idempotent across `start`/`stop`.
 *
 * Open/closed: a future RealtimeChatSource implementing the same shape
 * drops in without changing the store or the view.
 */
export interface ChatMessageSource {
  /** Begin emitting. Implementations should be a no-op if already started. */
  start(): void;
  /** Stop emitting. Implementations must cancel any pending schedules. */
  stop(): void;
  /** Add a listener. Returns an unsubscribe fn. */
  subscribe(listener: GlobalChatListener): () => void;
}
