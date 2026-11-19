"use client";

interface PersonaProductPanelProps {
  productId: string | null;
  personaId: string | null;
}

/**
 * Static product browsing surface that pairs with the avatar on
 * desktop. The panel is intentionally minimal for now: we render the
 * route's selected `productId` and let the avatar do the narrative
 * heavy lifting via its LiveKit audio.
 */
export function PersonaProductPanel({ productId, personaId }: PersonaProductPanelProps) {
  return (
    <section
      aria-label="Product details"
      className="flex h-full flex-col gap-4 overflow-y-auto p-6"
    >
      <header className="space-y-1">
        <h1 className="text-2xl font-semibold text-slate-900">Ask the avatar</h1>
        <p className="text-sm text-slate-600">
          Your personal AI shopping presenter. Use the mic to ask questions about anything on the
          page.
        </p>
      </header>

      <div className="rounded-xl border border-slate-200 bg-white p-4 shadow-sm">
        <h2 className="text-sm font-semibold uppercase tracking-wide text-slate-500">
          Now showing
        </h2>
        {productId ? (
          <p className="mt-2 text-base text-slate-900">
            Product&nbsp;
            <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-xs">{productId}</code>
          </p>
        ) : (
          <p className="mt-2 text-sm text-slate-500">
            No product selected. The avatar will offer general guidance.
          </p>
        )}
        {personaId && (
          <p className="mt-1 text-xs text-slate-500">
            Persona&nbsp;
            <code className="rounded bg-slate-100 px-1 py-0.5 font-mono text-xs">{personaId}</code>
          </p>
        )}
      </div>

      <ul className="space-y-2 text-sm text-slate-700">
        <li>• Ask about features, materials, fit, or shipping.</li>
        <li>• Compare with similar items the avatar mentions.</li>
        <li>• Ask the avatar to walk you through checkout.</li>
      </ul>
    </section>
  );
}
