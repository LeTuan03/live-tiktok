// Last-N rolling buffer for the consumer-feed global chat.
//
// The store sits between the message sources (currently just
// `SimulatedChatSource`; a real wire transport can plug in later via
// the same `ChatMessageSource` interface) and the React view. It keeps
// only the last `bufferSize` messages — the renderer only displays 5,
// but we keep a small extra to smooth the FLIP animation when a new
// message arrives while the user is mid-scroll.
//
// `sendLocal` is the composer path. A user-typed message is injected
// straight into the same stream so the user sees their own line land
// in the rolling window — no round-trip, no DB record, no auth.

import type { ChatMessageSource, GlobalChatMessage } from "./globalChat";

export interface GlobalChatStoreOptions {
  /** Sources to merge into one rolling buffer. The store starts each
   *  source on construction and stops them all on dispose. */
  sources: ChatMessageSource[];
  /** Cap kept in memory. View renders only the trailing 5; the extra
   *  margin lets the view do a clean enter/exit animation. */
  bufferSize?: number;
  /** Display name used for user-typed messages. */
  selfHandle?: string;
  /** Optional outbound fan-out (e.g. LiveKit text-stream publish).
   *  Called *in addition to* the local-inject path on every `sendLocal`
   *  so the user's message reaches other clients while the local
   *  rolling buffer paints their own copy immediately. */
  publishRemote?: (handle: string, body: string) => Promise<void> | void;
}

export type GlobalChatStoreListener = (messages: GlobalChatMessage[]) => void;

const DEFAULT_BUFFER = 8;
const DEFAULT_SELF_HANDLE = "anon";

export class GlobalChatStore {
  private readonly bufferSize: number;
  private readonly listeners = new Set<GlobalChatStoreListener>();
  /** Source → its unsubscribe fn. Sources are added/removed at runtime
   *  so the hot-swap of the simulator on product change doesn't tear
   *  down the rolling buffer. */
  private readonly sourceSubs = new Map<ChatMessageSource, () => void>();
  private readonly publishRemote?: (handle: string, body: string) => Promise<void> | void;
  private messages: GlobalChatMessage[] = [];
  private localCounter = 0;
  private selfHandle: string;
  private disposed = false;

  constructor(options: GlobalChatStoreOptions) {
    this.bufferSize = options.bufferSize ?? DEFAULT_BUFFER;
    this.selfHandle = options.selfHandle ?? DEFAULT_SELF_HANDLE;
    this.publishRemote = options.publishRemote;
    for (const source of options.sources) this.addSource(source);
  }

  /** Wire a new source into the rolling buffer. Idempotent — adding the
   *  same source twice is a no-op. The new source's messages merge with
   *  any already-buffered messages so the visible stream is continuous
   *  across simulator swaps. */
  addSource(source: ChatMessageSource): void {
    if (this.disposed) return;
    if (this.sourceSubs.has(source)) return;
    const unsub = source.subscribe((message) => {
      this.push(message);
    });
    this.sourceSubs.set(source, unsub);
    source.start();
  }

  /** Detach a source from the buffer. Stops the source (so a simulator
   *  cancels its pending timers) and unsubscribes the listener — but
   *  leaves the messages this source already pushed *inside* the
   *  rolling buffer. Those messages naturally age out as new sources
   *  push fresh entries. */
  removeSource(source: ChatMessageSource): void {
    const unsub = this.sourceSubs.get(source);
    if (!unsub) return;
    unsub();
    source.stop();
    this.sourceSubs.delete(source);
  }

  /** Snapshot — caller must treat as read-only. */
  getMessages(): GlobalChatMessage[] {
    return this.messages;
  }

  /** Subscribe to whole-array updates. Fires immediately with current
   *  state so React effects don't need a separate seed. */
  subscribe(listener: GlobalChatStoreListener): () => void {
    this.listeners.add(listener);
    listener(this.messages);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** Inject a user-composed message into the stream. No-op on empty /
   *  whitespace input. Trims + caps length. Also fans the message out
   *  to other clients via `publishRemote` when one is wired (LiveKit
   *  text stream in production; omitted in unit tests for the
   *  simulator-only path). */
  sendLocal(text: string): void {
    if (this.disposed) return;
    const trimmed = text.trim();
    if (!trimmed) return;
    const capped = trimmed.length > 200 ? trimmed.slice(0, 200) : trimmed;
    this.localCounter += 1;
    this.push({
      id: `local:${this.localCounter}`,
      authorKind: "self",
      authorHandle: this.selfHandle,
      text: capped,
      createdAt: Date.now(),
    });
    if (this.publishRemote) {
      // Fire-and-forget. LiveKit doesn't echo the sender's own message
      // back to themselves, so there's no duplicate to dedupe.
      void Promise.resolve(this.publishRemote(this.selfHandle, capped));
    }
  }

  setSelfHandle(handle: string): void {
    this.selfHandle = handle;
  }

  dispose(): void {
    if (this.disposed) return;
    this.disposed = true;
    for (const [source, unsub] of this.sourceSubs) {
      unsub();
      source.stop();
    }
    this.sourceSubs.clear();
    this.listeners.clear();
    this.messages = [];
  }

  private push(message: GlobalChatMessage): void {
    if (this.disposed) return;
    const next = this.messages.concat(message);
    if (next.length > this.bufferSize) {
      next.splice(0, next.length - this.bufferSize);
    }
    this.messages = next;
    for (const listener of this.listeners) listener(this.messages);
  }
}
