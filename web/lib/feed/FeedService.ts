import type { InteractionKind } from "./AutoscrollPolicy";

// Owns the persona queue, the current and next view-models, and the swap
// state machine.
//
// Lifecycle:
//   1. `start()` populates the initial window via `/feed/cursor`.
//   2. The view layer calls `markVisible()` when the active persona binds
//      so dwell measurement starts at the right moment.
//   3. `swipeForward()` / `swipeBack()` drive the state machine and fan out
//      to the room coordinator and `/feed/swipe`.
//   4. Failures land in `swapState = failed(...)`; the previous persona stays
//      rendered. `retry()` re-attempts the last failed swap.
//
// The wire shape matches the existing REST contract documented in
// `server/src/soshop/api/routes/feed.py` (`/feed/cursor`, `/feed/swipe`).

import { ApiError } from "@/lib/api";
import { SwapState, isInFlight } from "./SwapState";
import { TimeoutError, runWithTimeout } from "./runWithTimeout";

export { handleFromName, humanizeId, paletteForId } from "./palette";

// MARK: - Network surface

export type SwipeDirection = "forward" | "back";

/** One ordered media item attached to a product. Position is 0-based;
 *  by convention position 0 is the feature image, position 1 is the
 *  feature video (when present), and positions ≥2 are gallery images.
 *  See `server/migrations/0001_initial_schema.up.sql`. */
export interface FeedSlotProductMedia {
  position: number;
  kind: "image" | "video";
  url: string;
}

/** Product catalog snapshot embedded in a feed slot. Bundled from the
 *  server so the consumer card can render the PiP image and Shop CTA
 *  without a second `/products/{id}` fetch. The hero image is always
 *  the first entry of `media` — position 0 by server convention. */
export interface FeedSlotProduct {
  id: string;
  sku: string;
  title: string;
  description?: string | null;
  priceCents?: number | null;
  currency?: string | null;
  deepLinkUrl?: string | null;
  landingUrl?: string | null;
  /** Ordered gallery returned by `/feed/cursor` — already sorted by
   *  position (0 = feature image, 1 = feature video, 2+ = additional
   *  images). Empty only for products without any `product_media`
   *  rows. */
  media: FeedSlotProductMedia[];
}

/** View-model for a single feed page (web mirror of `PersonaSlot`). */
export interface PersonaSlot {
  id: string;
  displayName: string;
  handle: string;
  stillUrl?: string | null;
  avatarUrl?: string | null;
  /** Two-stop CSS gradient (`from`/`to` hex). */
  gradient: { from: string; to: string };
  accentColor: string;
  pitchCta?: string | null;
  isAd: boolean;
  isFavorited: boolean;
  isMuted: boolean;
  /** Product the active script promotes; `null` for freestyle scripts.
   *  The autoscroll policy uses this to filter `product_pitch` envelopes
   *  so a stale dismiss from the previous persona doesn't fire a swipe
   *  on the new card. */
  productId?: string | null;
  /** Full product details (image, title, price, deep link) — present
   *  when the slot carries a product. The card renders this as a PiP
   *  thumbnail next to the seller; tapping the PiP swaps the layouts. */
  product?: FeedSlotProduct | null;
  /** Script chosen by the ranker for this slot — flows into the LiveKit
   *  agent dispatch metadata. */
  scriptId?: string | null;
}

export interface FeedCursorPage {
  slots: PersonaSlot[];
  nextCursor: string | null;
}

export interface SwipeEvent {
  fromPersonaId: string;
  toPersonaId: string;
  /** Script chosen by the ranker for the incoming persona — flows into
   *  the `script_id` slot on the `/feed/swipe` payload so the server
   *  ranker / analytics see the actual script, not the persona UUID.
   *  `null` falls back to `toPersonaId` for backwards compatibility. */
  toScriptId: string | null;
  direction: SwipeDirection;
  dwellMs: number;
}

/**
 * Server-facing dependencies. Isolated behind an interface so the state
 * machine can be unit-tested without a real backend.
 */
export interface FeedClient {
  fetchCursor(cursor: string | null, limit: number): Promise<FeedCursorPage>;
  fetchToken(personaId: string): Promise<string>;
  reportSwipe(event: SwipeEvent): Promise<void>;
}

/**
 * LiveKit-side dependency. A single Room handle stays open across persona
 * swaps; `swap(token)` re-targets the dispatch and waits for the new
 * track to bind.
 */
export interface FeedRoomCoordinator {
  swap(token: string): Promise<void>;
}

// MARK: - FeedService

export interface FeedServiceConfig {
  prefetchTarget: number;
  refillThreshold: number;
  swapTimeoutMs: number;
  pageSize: number;
  /** "single_room" re-dispatches the agent into the same room. */
  mode: "single_room" | "ping_pong";
}

export const DEFAULT_FEED_CONFIG: FeedServiceConfig = {
  prefetchTarget: 5,
  refillThreshold: 3,
  swapTimeoutMs: 5_000,
  pageSize: 10,
  mode: "single_room",
};

export type FeedSnapshot = {
  current: PersonaSlot | null;
  next: PersonaSlot | null;
  history: ReadonlyArray<PersonaSlot>;
  forwardQueue: ReadonlyArray<PersonaSlot>;
  swapState: SwapState;
};

export type FeedServiceListener = (snapshot: FeedSnapshot) => void;

interface PendingSwap {
  outgoing: PersonaSlot;
  incoming: PersonaSlot;
  direction: SwipeDirection;
  recordHistory: boolean;
}

export class FeedService {
  readonly config: FeedServiceConfig;
  private readonly client: FeedClient;
  private readonly coordinator: FeedRoomCoordinator | null;
  private readonly now: () => number;

  private current: PersonaSlot | null = null;
  private nextSlot: PersonaSlot | null = null;
  private swapState: SwapState = SwapState.idle();

  private forwardQueue: PersonaSlot[] = [];
  private history: PersonaSlot[] = [];
  private nextCursor: string | null = null;
  private hasMorePages = true;
  private isFetchingPage = false;
  private refillInflight = false;
  private lastFailedAttempt: PendingSwap | null = null;

  /** `performance.now()` snapshot when the active persona finished binding. */
  private visibleSinceMs: number | null = null;

  private readonly listeners = new Set<FeedServiceListener>();
  private readonly interactionListeners = new Set<
    (event: { personaId: string; kind: InteractionKind }) => void
  >();

  constructor(opts: {
    client: FeedClient;
    coordinator?: FeedRoomCoordinator | null;
    config?: Partial<FeedServiceConfig>;
    now?: () => number;
    /** Pre-seeded head from `/feed/session?dispatch_head=true`. When
     *  supplied, `start()` skips the first `/feed/cursor` call and
     *  installs this slot directly as `current` — so the persona the
     *  user sees on first paint matches the persona the agent was
     *  just dispatched for. The follow-up cursor fetch uses
     *  `initialCursor` to avoid re-emitting the same persona. */
    seedHead?: { slot: PersonaSlot; initialCursor: string | null } | null;
  }) {
    this.client = opts.client;
    this.coordinator = opts.coordinator ?? null;
    this.config = { ...DEFAULT_FEED_CONFIG, ...(opts.config ?? {}) };
    this.now = opts.now ?? (() => performance.now());
    if (opts.seedHead) {
      this.current = opts.seedHead.slot;
      this.nextCursor = opts.seedHead.initialCursor;
      this.visibleSinceMs = this.now();
    }
  }

  // MARK: Subscription

  subscribe(listener: FeedServiceListener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot());
    return () => {
      this.listeners.delete(listener);
    };
  }

  snapshot(): FeedSnapshot {
    return {
      current: this.current,
      next: this.nextSlot,
      history: this.history,
      forwardQueue: this.forwardQueue,
      swapState: this.swapState,
    };
  }

  private emit() {
    const snap = this.snapshot();
    for (const listener of this.listeners) listener(snap);
  }

  // MARK: Lifecycle

  /** Pull the first page and seat the head persona as `current`.
   *
   *  Two paths converge here:
   *    - Cold start (no seeded head): fetch page 1 of `/feed/cursor`,
   *      promote the head, then top off the forward queue.
   *    - Seeded head (set by the constructor from `/feed/session?
   *      dispatch_head=true`): `current` is already populated;
   *      skip the head fetch but still top off the forward queue
   *      using the initial cursor token.
   *  Idempotent in both cases.
   */
  async start(): Promise<void> {
    if (this.current === null && this.forwardQueue.length === 0) {
      await this.fetchNextPage();
      this.promoteHeadIfNeeded();
    }
    this.ensureWindowFilled();
    this.emit();
  }

  markVisible(): void {
    this.visibleSinceMs = this.now();
  }

  markHidden(): void {
    this.visibleSinceMs = null;
  }

  // MARK: Interactions (autoscroll input)

  /** Record a user interaction with the active card. The autoscroll
   *  controller in the pager subscribes to these and feeds them into
   *  the per-card `AutoscrollPolicy`. Always tagged with the active
   *  persona at call time so a delayed handler doesn't bleed into the
   *  next card after a swipe. */
  recordInteraction(kind: InteractionKind): void {
    const personaId = this.current?.id;
    if (!personaId) return;
    for (const listener of this.interactionListeners) {
      listener({ personaId, kind });
    }
  }

  subscribeInteractions(
    listener: (event: { personaId: string; kind: InteractionKind }) => void,
  ): () => void {
    this.interactionListeners.add(listener);
    return () => {
      this.interactionListeners.delete(listener);
    };
  }

  // MARK: Swipe entry points

  async swipeForward(): Promise<void> {
    if (isInFlight(this.swapState)) return;
    const outgoing = this.current;
    const incoming = this.forwardQueue[0];
    if (!outgoing || !incoming) {
      this.swapState = SwapState.failed("No upcoming persona available.");
      this.emit();
      return;
    }
    this.forwardQueue.shift();

    const attempt: PendingSwap = {
      outgoing,
      incoming,
      direction: "forward",
      recordHistory: true,
    };
    const dwellMs = this.consumeDwellMs();
    await this.performSwap(attempt, dwellMs);
    this.ensureWindowFilled();
  }

  async swipeBack(): Promise<void> {
    if (isInFlight(this.swapState)) return;
    const outgoing = this.current;
    const previous = this.history[0];
    if (!outgoing || !previous) {
      this.swapState = SwapState.failed("No previous persona to return to.");
      this.emit();
      return;
    }
    this.history.shift();
    this.forwardQueue.unshift(outgoing);

    const attempt: PendingSwap = {
      outgoing,
      incoming: previous,
      direction: "back",
      recordHistory: false,
    };
    const dwellMs = this.consumeDwellMs();
    await this.performSwap(attempt, dwellMs);
  }

  async retry(): Promise<void> {
    if (this.swapState.kind !== "failed") return;
    const attempt = this.lastFailedAttempt;
    if (!attempt) {
      this.swapState = SwapState.idle();
      this.emit();
      return;
    }
    if (attempt.direction === "forward") {
      if (this.forwardQueue[0]?.id === attempt.incoming.id) {
        this.forwardQueue.shift();
      }
    } else {
      if (this.history[0]?.id === attempt.incoming.id) {
        this.history.shift();
      }
      if (this.forwardQueue[0]?.id !== attempt.outgoing.id) {
        this.forwardQueue.unshift(attempt.outgoing);
      }
    }
    this.lastFailedAttempt = null;
    await this.performSwap(attempt, 0);
    if (attempt.direction === "forward") this.ensureWindowFilled();
  }

  // MARK: State machine core

  private async performSwap(attempt: PendingSwap, _dwellMs: number): Promise<void> {
    const { outgoing, incoming, direction } = attempt;
    this.visibleSinceMs = null;

    let token: string;
    try {
      this.swapState =
        this.config.mode === "ping_pong"
          ? SwapState.preWarming(incoming.id)
          : SwapState.swapping(incoming.id);
      this.emit();
      token = await this.client.fetchToken(incoming.id);
    } catch (err) {
      this.swapState = SwapState.failed(friendlyMessage(err));
      this.restore(attempt);
      this.emit();
      return;
    }

    this.swapState = SwapState.swapping(incoming.id);
    this.emit();
    try {
      await runWithTimeout(this.config.swapTimeoutMs, async () => {
        if (this.coordinator) await this.coordinator.swap(token);
      });
    } catch (err) {
      const message =
        err instanceof TimeoutError
          ? "Persona swap timed out. Tap to retry."
          : friendlyMessage(err);
      this.swapState = SwapState.failed(message);
      this.restore(attempt);
      this.emit();
      return;
    }

    if (attempt.recordHistory) this.history.unshift(outgoing);
    this.current = incoming;
    this.refreshNext();
    this.swapState = SwapState.idle();
    this.lastFailedAttempt = null;
    this.emit();

    // Telemetry — non-fatal.
    try {
      await this.client.reportSwipe({
        fromPersonaId: outgoing.id,
        toPersonaId: incoming.id,
        toScriptId: incoming.scriptId ?? null,
        direction,
        dwellMs: _dwellMs,
      });
    } catch {
      /* swallow */
    }
  }

  private restore(attempt: PendingSwap) {
    if (attempt.direction === "forward") {
      this.forwardQueue.unshift(attempt.incoming);
    } else {
      if (this.forwardQueue[0]?.id === attempt.outgoing.id) {
        this.forwardQueue.shift();
      }
      this.history.unshift(attempt.incoming);
    }
    this.lastFailedAttempt = attempt;
  }

  // MARK: Window management

  private ensureWindowFilled() {
    if (!this.hasMorePages) return;
    if (this.forwardQueue.length >= this.config.refillThreshold) return;
    if (this.refillInflight) return;

    this.refillInflight = true;
    void (async () => {
      try {
        while (this.hasMorePages && this.forwardQueue.length < this.config.prefetchTarget) {
          await this.fetchNextPage();
        }
      } finally {
        this.refillInflight = false;
      }
    })();
  }

  private async fetchNextPage(): Promise<void> {
    if (this.isFetchingPage || !this.hasMorePages) return;
    this.isFetchingPage = true;
    try {
      const page = await this.client.fetchCursor(this.nextCursor, this.config.pageSize);
      this.forwardQueue.push(...page.slots);
      this.nextCursor = page.nextCursor;
      this.hasMorePages = page.nextCursor !== null && page.slots.length > 0;
      this.refreshNext();
      this.emit();
    } catch {
      // Background fetch errors don't flip swapState — the user keeps
      // consuming the existing queue, and we'll retry on the next swipe.
    } finally {
      this.isFetchingPage = false;
    }
  }

  private promoteHeadIfNeeded() {
    if (this.current !== null) return;
    const head = this.forwardQueue.shift();
    if (!head) return;
    this.current = head;
    this.refreshNext();
    this.visibleSinceMs = this.now();
  }

  private refreshNext() {
    this.nextSlot = this.forwardQueue[0] ?? null;
  }

  private consumeDwellMs(): number {
    const start = this.visibleSinceMs;
    if (start === null) return 0;
    const elapsed = this.now() - start;
    this.visibleSinceMs = null;
    return Math.max(0, Math.round(elapsed));
  }
}

// MARK: Helpers

function friendlyMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 0) return "You're offline. Tap to retry when back online.";
    return err.detail || "Couldn't load the next persona. Tap to retry.";
  }
  if (err instanceof Error) return err.message;
  return "Couldn't load the next persona. Tap to retry.";
}
