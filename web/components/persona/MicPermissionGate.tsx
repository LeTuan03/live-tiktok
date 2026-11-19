"use client";

import { useState } from "react";

interface MicPermissionGateProps {
  micPublished: boolean;
  onRequestMic: () => Promise<void>;
  onStopMic: () => Promise<void>;
  /** When the parent already has an unrecoverable error, show a short note instead. */
  disabled?: boolean;
}

type LocalError = { kind: "denied"; message: string } | { kind: "other"; message: string };

/**
 * Renders the explicit two-way-audio toggle. Browsers require a user
 * gesture to unlock `getUserMedia`, and the acceptance criteria
 * call out an "explicit mic permission UX" — so we surface a clear
 * Start/Stop button plus a denial-recovery message rather than auto-
 * publishing.
 *
 * Why the explicit gate: Safari and Chrome silently swallow
 * `getUserMedia` calls that aren't tied to a user gesture, so without an
 * explicit user-gesture affordance the request never reaches the system.
 * The denial-recovery message is the only path to recover from a
 * one-time block.
 */
export function MicPermissionGate({
  micPublished,
  onRequestMic,
  onStopMic,
  disabled = false,
}: MicPermissionGateProps) {
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<LocalError | null>(null);

  async function toggle() {
    setError(null);
    setPending(true);
    try {
      if (micPublished) {
        await onStopMic();
      } else {
        await onRequestMic();
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      // `NotAllowedError` is what Chrome/Safari throw when the user
      // declines the permission prompt or has it blocked at the OS level.
      const isDenial =
        err instanceof Error &&
        (err.name === "NotAllowedError" || err.name === "PermissionDeniedError");
      setError({
        kind: isDenial ? "denied" : "other",
        message: isDenial
          ? "Microphone access was blocked. Enable it in your browser's site settings to talk to the avatar."
          : message,
      });
    } finally {
      setPending(false);
    }
  }

  const label = micPublished
    ? "Stop microphone"
    : pending
      ? "Requesting permission…"
      : "Start microphone";

  return (
    <div className="flex flex-col gap-2">
      <button
        type="button"
        onClick={() => void toggle()}
        disabled={pending || disabled}
        className={`inline-flex items-center justify-center gap-2 rounded-full px-4 py-2 text-sm font-medium shadow transition ${
          micPublished
            ? "bg-rose-500 text-white hover:bg-rose-400"
            : "bg-white text-slate-900 hover:bg-slate-100"
        } disabled:cursor-not-allowed disabled:opacity-60`}
        aria-pressed={micPublished}
      >
        <span aria-hidden="true">{micPublished ? "■" : "🎙"}</span>
        {label}
      </button>
      {!micPublished && !error && (
        <p className="text-xs text-slate-300">
          The avatar can hear you when the mic is on. We&apos;ll ask your browser for permission.
        </p>
      )}
      {error && (
        <p
          role="alert"
          className={`text-xs ${error.kind === "denied" ? "text-amber-300" : "text-rose-300"}`}
        >
          {error.message}
        </p>
      )}
    </div>
  );
}
