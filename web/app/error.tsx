"use client";

// Per-segment error boundary. App Router invokes this whenever an
// unhandled error escapes any client component below the root layout —
// route components, server components, async boundaries. The root layout
// itself still renders (header chrome stays); only `children` is replaced
// by this view.

import { useEffect } from "react";

interface RouteErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function RouteError({ error, reset }: RouteErrorProps) {
  useEffect(() => {
    // No third-party error reporter on the public consumer surface; logging
    // keeps the failure visible via the browser console and Vercel logs.
    console.error("Route error boundary caught", error);
  }, [error]);

  return (
    <section
      role="alert"
      aria-live="polite"
      className="mx-auto flex min-h-[60dvh] max-w-md flex-col items-center justify-center gap-4 px-6 text-center"
    >
      <div aria-hidden className="text-4xl">
        ⚠️
      </div>
      <h2 className="text-xl font-bold">Couldn&apos;t load this page</h2>
      <p className="text-sm text-[var(--surface-fg-muted,rgb(148_163_184))]">
        {error.message || "Something went wrong. Try again in a moment."}
      </p>
      {error.digest && (
        <p className="font-mono text-xs text-[var(--surface-fg-muted,rgb(100_116_139))]">
          ref: {error.digest}
        </p>
      )}
      <button
        type="button"
        onClick={reset}
        data-testid="RouteError.retry"
        className="mt-2 rounded-full bg-amber-300 px-5 py-2 text-sm font-semibold text-slate-900 transition hover:bg-amber-200"
      >
        Try again
      </button>
    </section>
  );
}
