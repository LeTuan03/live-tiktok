"use client";
// Surfaces the non-success states the renderer can land in:
//   - error  → server unreachable / swap dispatch failed; offers retry.
//   - empty  → cursor returned zero slots.
//   - dailyCap → daily LemonSlice-minute cap reached.
//
// Rendered inside the active card so the persona's gradient/still
// reads through underneath. Background is a translucent ink scrim for
// legibility.

interface FeedErrorViewProps {
  state:
    | { kind: "error"; message: string }
    | { kind: "empty" }
    | { kind: "dailyCap"; resetsAt?: Date | null };
  onRetry?: () => void;
}

export function FeedErrorView({ state, onRetry }: FeedErrorViewProps) {
  const copy = copyForState(state);
  return (
    <div
      role="alert"
      aria-live="polite"
      className="absolute inset-0 z-30 flex items-center justify-center bg-black/70 px-8 text-center text-white backdrop-blur-sm"
    >
      <div className="flex max-w-sm flex-col items-center gap-3">
        <div className="text-3xl" aria-hidden>
          {copy.icon}
        </div>
        <h2 className="text-xl font-bold">{copy.headline}</h2>
        <p className="text-sm text-white/85">{copy.body}</p>
        {onRetry && (
          <button
            type="button"
            onClick={onRetry}
            data-testid="FeedErrorView.primaryButton"
            className="mt-2 rounded-full bg-amber-300 px-5 py-2 text-sm font-semibold text-slate-900 transition hover:bg-amber-200"
          >
            {copy.cta}
          </button>
        )}
      </div>
    </div>
  );
}

function copyForState(state: FeedErrorViewProps["state"]) {
  switch (state.kind) {
    case "error":
      return {
        icon: "📡",
        headline: "Having trouble connecting",
        body: state.message
          ? `${state.message} Give us a sec, we'll try again.`
          : "Give us a sec, we'll try again.",
        cta: "Try again",
      };
    case "empty":
      return {
        icon: "✨",
        headline: "No personas just yet",
        body: "We're warming up the feed. Pull to refresh in a moment.",
        cta: "Refresh",
      };
    case "dailyCap":
      return {
        icon: "🌙",
        headline: "That's a wrap for today",
        body: state.resetsAt
          ? `You've hit today's persona limit. Fresh feed unlocks at ${formatTime(state.resetsAt)}.`
          : "You've hit today's persona limit. Come back tomorrow for a fresh feed.",
        cta: "Catch you tomorrow",
      };
  }
}

function formatTime(date: Date): string {
  try {
    return date.toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" });
  } catch {
    return date.toISOString();
  }
}
