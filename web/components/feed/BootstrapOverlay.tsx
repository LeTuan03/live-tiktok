"use client";
// Cold-start surface for the consumer feed.
//
// Sits on top of the blurred host placeholder backdrop whenever there's
// no live video track on the active cell. Owns a 4-state derivation
// off `useAgentSession` + the `hasActiveTrack` prop:
//
//   idle     — no session active, no mutation in flight → CTA pill
//   starting — POST /agents/session in flight → loading card
//   warming  — POST resolved (or polled status reports active) but
//              the LiveKit track hasn't bound yet → loading card with
//              dynamic status text
//   error    — last POST rejected → retry card
//
// Returns `null` when `hasActiveTrack` is true so the live video paints
// unobstructed. Tests can mount this with `hasActiveTrack={false}` and
// drive the visible state by mocking `useAgentSession`.

import { useCallback, useEffect, useState } from "react";
import { useAgentSession } from "@/lib/agents/useAgentSession";

export interface BootstrapOverlayProps {
  /** When true the overlay hides — the live frames carry the surface. */
  hasActiveTrack: boolean;
}

export type BootstrapOverlayVisualState = "idle" | "starting" | "warming" | "error";

/** Upper bound on the warming state before we surface a retry path.
 *  Agent warmup is ~10–30s on a cold cache; 45s gives comfortable
 *  margin without leaving the viewer staring at a never-ending
 *  "Almost ready…". If the LiveKit `TrackSubscribed` event never
 *  fires (network drop, agent crash, SFU event loss) the rotating
 *  copy would otherwise loop forever on the placeholder. */
const WARMING_TIMEOUT_MS = 45_000;

function deriveVisualState({
  hasActiveTrack,
  isStarting,
  sessionActive,
  startError,
  warmingTimedOut,
}: {
  hasActiveTrack: boolean;
  isStarting: boolean;
  sessionActive: boolean;
  startError: Error | null;
  warmingTimedOut: boolean;
}): BootstrapOverlayVisualState | "hidden" {
  if (hasActiveTrack) return "hidden";
  if (isStarting) return "starting";
  if (sessionActive && warmingTimedOut) return "error";
  if (sessionActive) return "warming";
  if (startError) return "error";
  return "idle";
}

export function BootstrapOverlay({ hasActiveTrack }: BootstrapOverlayProps) {
  const { status, isStarting, startError, start } = useAgentSession();
  const sessionActive = status?.active ?? false;

  // Watchdog: once we enter the warming state, start a single timer.
  // If `hasActiveTrack` doesn't flip before it expires, we surface
  // the retry card so the user has an out. The flag resets whenever
  // we leave warming (track binds or session ends); a user-initiated
  // retry resets it explicitly through `handleStart` below — relying
  // on the effect alone would miss that case, because `sessionActive`
  // stays `true` across the retry (the POST replaces the cache value
  // with the same boolean and the dep-array check is a no-op).
  const [warmingTimedOut, setWarmingTimedOut] = useState(false);
  useEffect(() => {
    if (hasActiveTrack || !sessionActive) {
      setWarmingTimedOut(false);
      return;
    }
    const id = setTimeout(() => setWarmingTimedOut(true), WARMING_TIMEOUT_MS);
    return () => clearTimeout(id);
  }, [hasActiveTrack, sessionActive]);

  const handleStart = useCallback(() => {
    setWarmingTimedOut(false);
    start();
  }, [start]);

  const visual = deriveVisualState({
    hasActiveTrack,
    isStarting,
    sessionActive,
    startError: startError ?? null,
    warmingTimedOut,
  });

  if (visual === "hidden") return null;

  // Anchored to the bottom third of the 9:16 column. Above the pinned-
  // product card slot (which lives at `bottom-3` / `bottom-6`) and well
  // clear of the top SoShop wordmark — the thumb-zone sweet spot for a
  // one-handed mobile hold without colliding with platform chrome.
  return (
    <div
      data-testid="BootstrapOverlay.root"
      data-state={visual}
      className="pointer-events-none absolute inset-x-0 bottom-[28%] z-40 flex justify-center px-6"
    >
      {visual === "idle" || visual === "error" ? (
        <IdleCard state={visual} onStart={handleStart} />
      ) : (
        <LoadingCard state={visual} />
      )}
    </div>
  );
}

function IdleCard({ state, onStart }: { state: "idle" | "error"; onStart: () => void }) {
  return (
    <div className="pointer-events-auto flex w-full max-w-sm flex-col items-center gap-3 text-center">
      {state === "error" && (
        <p
          role="alert"
          className="rounded-lg bg-rose-500/15 px-4 py-2 text-sm text-rose-50 ring-1 ring-rose-300/30 backdrop-blur"
        >
          Couldn&apos;t start the stream. Tap to retry.
        </p>
      )}
      <button
        type="button"
        onClick={onStart}
        data-testid="BootstrapOverlay.sellMeStuffButton"
        className="w-full rounded-full bg-white px-8 py-4 text-lg font-bold text-black shadow-[0_8px_28px_rgba(0,0,0,0.45)] transition hover:bg-white/95 active:scale-[0.98] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-white"
      >
        {state === "error" ? "Try again" : "Sell me stuff"}
      </button>
    </div>
  );
}

/**
 * Loading messages, rotated by elapsed-time-since-tap. Sourced from
 * Pencil & Paper's wait-feedback guidance: dynamic + contextual >
 * static + indeterminate. Each phrase is intentionally descriptive of
 * *what* is happening so the screen-reader announcement (`aria-live`)
 * tells the user something useful — not just "loading."
 */
const WARMING_MESSAGES = [
  { fromMs: 0, text: "Warming up your stream…" },
  { fromMs: 4_000, text: "Tuning in the host…" },
  { fromMs: 9_000, text: "Connecting to the live room…" },
  { fromMs: 16_000, text: "Almost ready…" },
] as const;

function useElapsedMs(): number {
  const [ms, setMs] = useState(0);
  useEffect(() => {
    const start = Date.now();
    const id = setInterval(() => setMs(Date.now() - start), 500);
    return () => clearInterval(id);
  }, []);
  return ms;
}

function LoadingCard({ state }: { state: "starting" | "warming" }) {
  const elapsedMs = useElapsedMs();
  const message =
    [...WARMING_MESSAGES].reverse().find((m) => elapsedMs >= m.fromMs)?.text ??
    WARMING_MESSAGES[0].text;

  return (
    <div
      role="status"
      aria-live="polite"
      aria-busy="true"
      data-testid="BootstrapOverlay.loadingCard"
      className="pointer-events-auto flex w-full max-w-sm flex-col items-center gap-4 rounded-3xl bg-black/55 px-6 py-5 text-center text-white shadow-[0_8px_28px_rgba(0,0,0,0.5)] ring-1 ring-white/10 backdrop-blur-md"
    >
      <PulseDots />
      <p className="text-base font-semibold leading-tight">{message}</p>
      <p className="sr-only">
        {/* Screen readers prefer the explicit state over the rotating
            marketing copy — the live region above already announces the
            current phase. */}
        {state === "starting" ? "Starting agent session." : "Awaiting live video."}
      </p>
    </div>
  );
}

/**
 * Three brand-white dots pulsing with a 180 ms phase offset. A
 * determinate-feeling animation (each dot deterministically peaks then
 * decays) reads as progress without making time-to-completion promises
 * we can't keep — the agent warmup is ~10–30 s depending on cold-cache
 * state, and a percent bar would lie.
 */
function PulseDots() {
  return (
    <div aria-hidden className="flex items-center justify-center gap-2">
      {[0, 1, 2].map((i) => (
        <span
          key={i}
          className="block h-2.5 w-2.5 rounded-full bg-white"
          style={{
            animation: "bootstrap-overlay-pulse 1.2s ease-in-out infinite",
            animationDelay: `${i * 0.18}s`,
          }}
        />
      ))}
      <style jsx>{`
        @keyframes bootstrap-overlay-pulse {
          0%,
          80%,
          100% {
            transform: scale(0.6);
            opacity: 0.5;
          }
          40% {
            transform: scale(1);
            opacity: 1;
          }
        }
        @media (prefers-reduced-motion: reduce) {
          span {
            animation: none !important;
            opacity: 0.85;
            transform: none;
          }
        }
      `}</style>
    </div>
  );
}
