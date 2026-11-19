// Autoscroll-on-ad-completion policy for the consumer feed.
//
// Spec:
//   1. No interaction → autoscroll on pitchEnd (with a small grace delay).
//   2. Interacted    → stay until userScrolled. Safety cap fires after
//      `safetyCapMs` of no further interaction.
//   3. userScrolled  → cancel anything pending.
//
// Pure: no React, no DOM, no Room. Inject `now()` and `setTimer()` so
// tests are deterministic.
//
// Lifecycle: one policy per active feed card. Construct on
// `cardActivated`, dispose on `cardDeactivated` (also resets timers).

export type InteractionKind = "chat" | "product_tap" | "favorite" | "mute" | "profile" | "buy";

export type AutoscrollState =
  | { kind: "idle" }
  | { kind: "watching" }
  | { kind: "interacted"; lastInteractionAt: number }
  | { kind: "pendingAdvance"; firesAt: number; reason: "pitchEnd" }
  | {
      kind: "interactedAfterPitch";
      pitchEndAt: number;
      lastInteractionAt: number;
    }
  | { kind: "cancelled" };

export interface AutoscrollPolicyOptions {
  /** Delay after `pitchEnd` (with no interaction) before advancing. */
  graceMs?: number;
  /** Max dwell after the last interaction before forcing advance. */
  safetyCapMs?: number;
  now?: () => number;
  setTimer?: (fn: () => void, ms: number) => () => void;
  /** Fired exactly once per policy lifetime when an advance is requested. */
  requestAdvance: () => void;
  /** Optional log sink for telemetry (signpost-equivalent). */
  onTransition?: (state: AutoscrollState) => void;
}

export const DEFAULT_GRACE_MS = 1500;
export const DEFAULT_SAFETY_CAP_MS = 60_000;

const defaultTimer = (fn: () => void, ms: number): (() => void) => {
  const id = setTimeout(fn, ms);
  return () => clearTimeout(id);
};

export class AutoscrollPolicy {
  private state: AutoscrollState = { kind: "idle" };
  private cancelTimer: (() => void) | null = null;
  private advanceFired = false;
  private readonly graceMs: number;
  private readonly safetyCapMs: number;
  private readonly now: () => number;
  private readonly setTimer: (fn: () => void, ms: number) => () => void;
  private readonly requestAdvance: () => void;
  private readonly onTransition?: (state: AutoscrollState) => void;

  constructor(opts: AutoscrollPolicyOptions) {
    this.graceMs = opts.graceMs ?? DEFAULT_GRACE_MS;
    this.safetyCapMs = opts.safetyCapMs ?? DEFAULT_SAFETY_CAP_MS;
    this.now = opts.now ?? (() => Date.now());
    this.setTimer = opts.setTimer ?? defaultTimer;
    this.requestAdvance = opts.requestAdvance;
    this.onTransition = opts.onTransition;
  }

  getState(): AutoscrollState {
    return this.state;
  }

  cardActivated(): void {
    if (this.state.kind !== "idle") return;
    this.transition({ kind: "watching" });
  }

  pitchEnd(): void {
    if (this.state.kind === "watching") {
      const firesAt = this.now() + this.graceMs;
      this.armTimer(this.graceMs, () => this.fireAdvance());
      this.transition({ kind: "pendingAdvance", firesAt, reason: "pitchEnd" });
      return;
    }
    if (this.state.kind === "interacted") {
      const pitchEndAt = this.now();
      this.armSafetyCap(pitchEndAt);
      this.transition({
        kind: "interactedAfterPitch",
        pitchEndAt,
        lastInteractionAt: this.state.lastInteractionAt,
      });
      return;
    }
  }

  interacted(_kind: InteractionKind): void {
    const at = this.now();
    if (this.state.kind === "watching") {
      this.armSafetyCap(at);
      this.transition({ kind: "interacted", lastInteractionAt: at });
      return;
    }
    if (this.state.kind === "interacted") {
      this.armSafetyCap(at);
      this.transition({ kind: "interacted", lastInteractionAt: at });
      return;
    }
    if (this.state.kind === "pendingAdvance") {
      this.armSafetyCap(at);
      this.transition({
        kind: "interactedAfterPitch",
        pitchEndAt: at,
        lastInteractionAt: at,
      });
      return;
    }
    if (this.state.kind === "interactedAfterPitch") {
      this.armSafetyCap(at);
      this.transition({
        kind: "interactedAfterPitch",
        pitchEndAt: this.state.pitchEndAt,
        lastInteractionAt: at,
      });
      return;
    }
  }

  userScrolled(): void {
    if (this.state.kind === "cancelled") return;
    this.clearTimer();
    this.transition({ kind: "cancelled" });
  }

  cardDeactivated(): void {
    this.clearTimer();
    if (this.state.kind !== "cancelled") {
      this.transition({ kind: "cancelled" });
    }
  }

  private armTimer(ms: number, fn: () => void): void {
    this.clearTimer();
    this.cancelTimer = this.setTimer(fn, ms);
  }

  private armSafetyCap(fromAt: number): void {
    this.armTimer(this.safetyCapMs, () => {
      const elapsed = this.now() - fromAt;
      if (elapsed + 1 < this.safetyCapMs) {
        this.armTimer(this.safetyCapMs - elapsed, () => this.fireAdvance());
        return;
      }
      this.fireAdvance();
    });
  }

  private clearTimer(): void {
    if (this.cancelTimer) {
      this.cancelTimer();
      this.cancelTimer = null;
    }
  }

  private fireAdvance(): void {
    if (this.advanceFired || this.state.kind === "cancelled") return;
    this.advanceFired = true;
    this.clearTimer();
    try {
      this.requestAdvance();
    } finally {
      this.transition({ kind: "cancelled" });
    }
  }

  private transition(next: AutoscrollState): void {
    this.state = next;
    this.onTransition?.(next);
  }
}
