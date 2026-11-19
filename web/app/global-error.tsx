"use client";

// Last-resort error boundary for the root layout itself (App Router
// convention). Next.js falls back to this only when an error escapes the
// per-segment `error.tsx` boundary — typically because the root layout
// threw during render. Because the root layout's own `<html>` / `<body>`
// were not rendered, this component must own them.

interface GlobalErrorProps {
  error: Error & { digest?: string };
  reset: () => void;
}

export default function GlobalError({ error, reset }: GlobalErrorProps) {
  // No third-party error reporter is wired here; logging keeps the failure
  // visible via the browser console and Vercel runtime logs.
  if (typeof window !== "undefined") {
    console.error("GlobalError caught", error);
  }

  return (
    <html lang="en">
      <body className="min-h-screen bg-slate-950 font-sans text-slate-100 antialiased">
        <main
          role="alert"
          className="mx-auto flex min-h-screen max-w-md flex-col items-center justify-center gap-4 px-6 text-center"
        >
          <h1 className="text-2xl font-bold">Something went wrong</h1>
          <p className="text-sm text-slate-300">
            {error.message || "An unexpected error occurred."}
          </p>
          {error.digest && <p className="font-mono text-xs text-slate-500">ref: {error.digest}</p>}
          <button
            type="button"
            onClick={reset}
            className="rounded-full bg-amber-300 px-5 py-2 text-sm font-semibold text-slate-900 transition hover:bg-amber-200"
          >
            Try again
          </button>
        </main>
      </body>
    </html>
  );
}
