// Auto-advance pagination policy for the full-screen product gallery
// shown while the seller avatar is in PiP (`ProductFocusView`).
//
// UX spec — researched against WCAG 2.2.2 "Pause, Stop, Hide", Nielsen
// Norman Group's auto-forwarding guidance, and mainstream carousel
// defaults (Bootstrap/Swiper ship ~5s):
//
//   1. Dwell `intervalMs` (default 5s) on each image, then advance to
//      the next; the caller wraps after the last so rotation is
//      continuous.
//   2. The FIRST manual gallery gesture (swipe/scroll, dot, chevron)
//      stops autoplay for the rest of this product's focus session and
//      it does NOT resume — never fight a user who has taken control.
//   3. Pause (resumable) while the pointer hovers the gallery, and while
//      the tab is hidden (Page Visibility), so images don't silently
//      march past an unfocused viewer.
//
// The caller is responsible for the two preconditions this class does
// not police: don't `start()` when `prefers-reduced-motion: reduce` is
// set, or when there are fewer than two images.
//
// Pure: no React, no DOM, no timers of its own beyond the injected
// `setTimer`. Inject `setTimer` for deterministic tests, mirroring the
// `AutoscrollPolicy` value type so the two feed timers read alike.

export interface GalleryAutoplayOptions {
  /** Dwell per image before advancing. */
  intervalMs?: number;
  /** Injectable timer so tests stay deterministic. Returns a canceller. */
  setTimer?: (fn: () => void, ms: number) => () => void;
  /** Advance to the next slide. Called once per tick; the caller wraps
   *  the index and performs the scroll. */
  advance: () => void;
  /** Optional sink fired when the running/paused edge flips (telemetry
   *  or a progress affordance). */
  onRunningChange?: (running: boolean) => void;
}

export const DEFAULT_GALLERY_AUTOPLAY_MS = 5000;

const defaultTimer = (fn: () => void, ms: number): (() => void) => {
  const id = setTimeout(fn, ms);
  return () => clearTimeout(id);
};

export class GalleryAutoplay {
  private started = false;
  /** Permanent: set once the user takes manual control. */
  private deactivated = false;
  private hovered = false;
  private hidden = false;
  private running = false;
  private cancelTimer: (() => void) | null = null;
  private readonly intervalMs: number;
  private readonly setTimer: (fn: () => void, ms: number) => () => void;
  private readonly advance: () => void;
  private readonly onRunningChange?: (running: boolean) => void;

  constructor(opts: GalleryAutoplayOptions) {
    this.intervalMs = opts.intervalMs ?? DEFAULT_GALLERY_AUTOPLAY_MS;
    this.setTimer = opts.setTimer ?? defaultTimer;
    this.advance = opts.advance;
    this.onRunningChange = opts.onRunningChange;
  }

  /** Begin (or no-op if already deactivated). Idempotent. */
  start(): void {
    if (this.deactivated) return;
    this.started = true;
    this.sync();
  }

  /** Permanent stop — the user took manual control (Rule 2). Never
   *  resumes; the caller rebuilds a fresh instance for the next product. */
  stop(): void {
    if (this.deactivated) return;
    this.deactivated = true;
    this.sync();
  }

  setHovered(hovered: boolean): void {
    if (this.hovered === hovered) return;
    this.hovered = hovered;
    this.sync();
  }

  setHidden(hidden: boolean): void {
    if (this.hidden === hidden) return;
    this.hidden = hidden;
    this.sync();
  }

  /** Tear down on unmount / product swap. Equivalent to a permanent stop
   *  but without claiming the user interacted. */
  dispose(): void {
    this.started = false;
    this.clearTimer();
    this.setRunning(false);
  }

  isRunning(): boolean {
    return this.running;
  }

  private get shouldRun(): boolean {
    return this.started && !this.deactivated && !this.hovered && !this.hidden;
  }

  /** Reconcile timer + running flag with the current gate inputs. */
  private sync(): void {
    const run = this.shouldRun;
    if (run && !this.cancelTimer) {
      this.arm();
    } else if (!run && this.cancelTimer) {
      this.clearTimer();
    }
    this.setRunning(run);
  }

  private arm(): void {
    this.cancelTimer = this.setTimer(() => {
      // The timer self-clears as it fires; re-check the gate in case it
      // closed during the dwell, then advance and re-arm for the loop.
      this.cancelTimer = null;
      if (!this.shouldRun) {
        this.setRunning(false);
        return;
      }
      this.advance();
      this.arm();
    }, this.intervalMs);
  }

  private clearTimer(): void {
    if (this.cancelTimer) {
      this.cancelTimer();
      this.cancelTimer = null;
    }
  }

  private setRunning(running: boolean): void {
    if (this.running === running) return;
    this.running = running;
    this.onRunningChange?.(running);
  }
}
