"use client";

import { useEffect, useState } from "react";
import { GatedAction } from "@/components/auth/GatedAction";
import type { PinnedProduct } from "./types";

interface PinnedProductCardProps {
  product: PinnedProduct;
  /** Fires when the user taps the card body. The caller swaps the
   *  product into the full-bleed slot (avatar shrinks to PiP) and
   *  renders the in-app gallery. When omitted, the card body is
   *  non-interactive (no external navigation either — SoShop never
   *  navigates outside the app for product detail). */
  onExpand?: () => void;
}

function formatPrice(cents: number, currency: string): string {
  try {
    return new Intl.NumberFormat(undefined, {
      style: "currency",
      currency,
    }).format(cents / 100);
  } catch {
    return `$${(cents / 100).toFixed(2)}`;
  }
}

/**
 * Renders either a structured catalog pin (price + CTA label) or a
 * free-form pitch (`cta` text). Field priority: structured price wins
 * when both are present, otherwise the free-form `cta` copy carries the
 * price and copy itself.
 *
 * Tap targets:
 * - Card body — fires `onExpand`, switching the cell into "product
 *   focus" mode: the seller PiPs into the corner and the product's
 *   gallery fills the screen. Tapping never navigates outside the
 *   app — purchase + detail happen in-app.
 * - "Buy now" button — fires `handleBuy` through `<GatedAction>`, which
 *   shows the simulated purchase confirmation. The backend is open
 *   access, so the purchase flow needs no sign-in.
 */
export function PinnedProductCard({ product, onExpand }: PinnedProductCardProps) {
  const [purchaseConfirmation, setPurchaseConfirmation] = useState<string | null>(null);

  // Clear the post-purchase confirmation when a fresh product rotates
  // into this slot — otherwise the new product's title is shown above
  // a stale "Purchase confirmed: <previous product>" banner.
  useEffect(() => {
    setPurchaseConfirmation(null);
  }, [product.productId]);

  const ctaButtonLabel = product.ctaLabel ?? "Shop now";

  // Subtitle priority: structured price (when fully specified), then
  // free-form CTA copy, then empty. A pitch frequently puts the price
  // INTO the cta string ("Tap to shop · $18") so we don't need to
  // reformat, just render it.
  const subtitle =
    typeof product.priceCents === "number" && product.currency
      ? formatPrice(product.priceCents, product.currency)
      : (product.cta ?? null);

  function handleBuy() {
    const priceCopy =
      typeof product.priceCents === "number" && product.currency
        ? ` for ${formatPrice(product.priceCents, product.currency)}`
        : "";
    setPurchaseConfirmation(`Purchase confirmed: ${product.title}${priceCopy}.`);
  }

  const bodyClassName = "flex min-w-0 flex-1 items-center gap-3 text-left transition";
  const bodyChildren = (
    <>
      {product.imageUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={product.imageUrl}
          alt=""
          className="h-14 w-14 flex-shrink-0 rounded-lg object-cover"
        />
      ) : (
        <div className="h-14 w-14 flex-shrink-0 rounded-lg bg-slate-200" aria-hidden />
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold text-slate-900">{product.title}</div>
        {subtitle && <div className="truncate text-xs font-medium text-slate-600">{subtitle}</div>}
      </div>
    </>
  );

  return (
    <div className="flex w-full flex-col gap-2 rounded-xl bg-white/95 p-3 shadow-lg ring-1 ring-black/5 backdrop-blur">
      <div className="flex items-center gap-3">
        {onExpand != null ? (
          <button
            type="button"
            onClick={onExpand}
            aria-label={`View ${product.title}`}
            className={`${bodyClassName} hover:opacity-90`}
          >
            {bodyChildren}
          </button>
        ) : (
          <div className={bodyClassName}>{bodyChildren}</div>
        )}
        <GatedAction onAction={handleBuy}>
          <button
            type="button"
            aria-label={`Buy ${product.title}`}
            className="flex-shrink-0 rounded-full bg-brand px-3 py-1.5 text-xs font-semibold text-white transition hover:opacity-90"
          >
            {ctaButtonLabel}
          </button>
        </GatedAction>
      </div>
      {purchaseConfirmation && (
        <div
          role="status"
          aria-live="polite"
          className="flex items-center justify-between gap-2 rounded-lg bg-emerald-50 px-3 py-2 text-xs font-medium text-emerald-900 ring-1 ring-emerald-200"
        >
          <span className="truncate">{purchaseConfirmation}</span>
          <button
            type="button"
            onClick={() => setPurchaseConfirmation(null)}
            className="flex-shrink-0 rounded px-1 text-emerald-900/70 hover:text-emerald-900"
            aria-label="Dismiss"
          >
            ✕
          </button>
        </div>
      )}
    </div>
  );
}
