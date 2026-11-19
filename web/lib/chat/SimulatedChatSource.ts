// Observer-pattern `ChatMessageSource` that drives the demo stream.
//
// One source per active channel/product. The store owns the source and
// calls `start()` when the product changes; the source schedules each
// scripted tick via the injected scheduler. The simulator never holds
// React state — it's a plain class so the same instance survives an
// Effect re-run and unit tests can drive it with a fake scheduler.

import { buildScript, type ScriptedTick } from "./CommentScript";
import type { ChatMessageSource, GlobalChatListener, GlobalChatMessage } from "./globalChat";

export interface Scheduler {
  setTimeout(handler: () => void, ms: number): ScheduledHandle;
  clearTimeout(handle: ScheduledHandle): void;
  now(): number;
}

export type ScheduledHandle = unknown;

export const realScheduler: Scheduler = {
  setTimeout(handler, ms) {
    return globalThis.setTimeout(handler, ms);
  },
  clearTimeout(handle) {
    globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>);
  },
  now() {
    return Date.now();
  },
};

export interface SimulatedChatSourceOptions {
  productId: string;
  /** Override the script duration (default: 30 s in `buildScript`). */
  durationMs?: number;
  /** DI seam for tests. */
  scheduler?: Scheduler;
  /** DI seam for tests. */
  script?: ScriptedTick[];
}

export class SimulatedChatSource implements ChatMessageSource {
  private readonly listeners = new Set<GlobalChatListener>();
  private readonly scheduler: Scheduler;
  private readonly script: ScriptedTick[];
  private readonly productId: string;
  private handles: ScheduledHandle[] = [];
  private started = false;
  private disposed = false;

  constructor(options: SimulatedChatSourceOptions) {
    this.scheduler = options.scheduler ?? realScheduler;
    this.productId = options.productId;
    this.script =
      options.script ??
      buildScript({ productId: options.productId, durationMs: options.durationMs });
  }

  start(): void {
    if (this.disposed) return;
    if (this.started) return;
    this.started = true;
    for (let i = 0; i < this.script.length; i++) {
      const tick = this.script[i];
      const handle = this.scheduler.setTimeout(() => {
        if (this.disposed) return;
        const message: GlobalChatMessage = {
          id: `script:${this.productId}:${i}`,
          authorKind: "viewer",
          authorHandle: tick.authorHandle,
          text: tick.text,
          createdAt: this.scheduler.now(),
        };
        for (const listener of this.listeners) listener(message);
      }, tick.atMs);
      this.handles.push(handle);
    }
  }

  stop(): void {
    for (const handle of this.handles) this.scheduler.clearTimeout(handle);
    this.handles = [];
    this.started = false;
  }

  subscribe(listener: GlobalChatListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  dispose(): void {
    this.stop();
    this.listeners.clear();
    this.disposed = true;
  }
}
