"use client";

// Whatnot-style pinned-product PiP. Pinned to the corner over the
// persona's live track; auto-collapses from "hero" to "badge" so the
// creator gets the screen back. Tapping the badge re-expands; tapping
// the expanded hero swaps the product into the full-bleed slot.
//
// Layout-affecting numbers live here (not on the caller) because they
// describe the PiP's own affordance — `PersonaCard` only needs to mount
// it. The DraggablePiP wrapper handles pan + corner-snap.

import { useEffect, useState } from "react";
import { DraggablePiP } from "@/components/feed/DraggablePiP";
import type { FeedSlotProduct } from "@/lib/feed/FeedService";

const EXPANDED_W = 160;
const EXPANDED_H = 176;
const COLLAPSED_W = 132;
const COLLAPSED_H = 64;
// Desktop gets a 2x PiP. Phones already use ~half the viewport width for the
// expanded hero, so scaling there would cover the creator. Matches Tailwind's
// `lg` breakpoint so the threshold lines up with the rest of the layout.
const DESKTOP_SCALE = 2;
const DESKTOP_MQ = "(min-width: 1024px)";
// Time the expanded product card stays open before auto-collapsing to a
// compact thumb+price badge. Matches Whatnot's pinned-product behavior:
// hero the product briefly, then yield screen real estate back to the
// creator. Tap the badge to re-expand.
const COLLAPSE_MS = 3000;

export interface ProductPiPProps {
  product: FeedSlotProduct;
  /** Fired when the user taps the expanded hero — caller swaps the
   *  product into the full-bleed slot. */
  onExpand: () => void;
}

export function ProductPiP({ product, onExpand }: ProductPiPProps) {
  const [collapsed, setCollapsed] = useState(false);
  const isDesktop = useIsDesktop();
  useEffect(() => {
    if (collapsed) return;
    const id = window.setTimeout(() => setCollapsed(true), COLLAPSE_MS);
    return () => window.clearTimeout(id);
  }, [collapsed]);

  const price =
    typeof product.priceCents === "number" && product.currency
      ? formatPrice(product.priceCents, product.currency)
      : null;

  // Feature image is position 0 of the gallery (server convention,
  // enforced in `_load_products`). Skipping over video entries so a
  // gallery whose first non-image is at position 1 still surfaces a
  // visual in the PiP thumbnail.
  const heroImageUrl = product.media?.find((m) => m.kind === "image")?.url ?? null;

  const scale = isDesktop ? DESKTOP_SCALE : 1;
  const width = (collapsed ? COLLAPSED_W : EXPANDED_W) * scale;
  const height = (collapsed ? COLLAPSED_H : EXPANDED_H) * scale;

  return (
    <DraggablePiP
      width={width}
      height={height}
      initialCorner="top-right"
      // Default `insetBottom` (160) is sized for layouts with a tall pinned
      // product card; here the scene only has the ActionRail on the right,
      // and the user expects bottom corners to land at the actual bottom.
      insetBottom={24}
      // Desktop: no corner enforcement — rest wherever dropped.
      snapToCorners={!isDesktop}
      ariaLabel={collapsed ? `View ${product.title}` : `Shop ${product.title}`}
      onTap={() => {
        if (collapsed) {
          setCollapsed(false);
        } else {
          onExpand();
        }
      }}
    >
      {collapsed ? (
        <CollapsedBadge heroImageUrl={heroImageUrl} title={product.title} price={price} />
      ) : (
        <ExpandedHero heroImageUrl={heroImageUrl} title={product.title} price={price} />
      )}
    </DraggablePiP>
  );
}

interface BadgeProps {
  heroImageUrl: string | null;
  title: string;
  price: string | null;
}

function CollapsedBadge({ heroImageUrl, title, price }: BadgeProps) {
  return (
    <div className="flex h-full w-full items-center gap-2 overflow-hidden rounded-full bg-white/95 px-1.5 py-1 text-left shadow-xl ring-1 ring-black/10 backdrop-blur">
      {heroImageUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={heroImageUrl}
          alt=""
          className="h-12 w-12 flex-shrink-0 rounded-full object-cover"
          draggable={false}
        />
      ) : (
        <div className="h-12 w-12 flex-shrink-0 rounded-full bg-slate-200" aria-hidden />
      )}
      <div className="flex min-w-0 flex-1 flex-col justify-center pr-2">
        <div className="truncate text-[11px] font-semibold leading-tight text-slate-900">
          {title}
        </div>
        {price && (
          <div className="truncate text-[12px] font-bold leading-tight text-slate-900">{price}</div>
        )}
      </div>
    </div>
  );
}

function ExpandedHero({ heroImageUrl, title, price }: BadgeProps) {
  return (
    <div className="flex h-full w-full flex-col overflow-hidden rounded-xl bg-white/95 text-left shadow-xl ring-1 ring-black/10 backdrop-blur">
      {heroImageUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={heroImageUrl}
          alt=""
          className="h-32 w-full flex-shrink-0 object-cover"
          draggable={false}
        />
      ) : (
        <div className="h-32 w-full flex-shrink-0 bg-slate-200" aria-hidden />
      )}
      <div className="flex flex-1 flex-col justify-center px-2 py-1">
        <div className="truncate text-[12px] font-semibold text-slate-900">{title}</div>
        {price && <div className="truncate text-[11px] font-medium text-slate-600">{price}</div>}
      </div>
    </div>
  );
}

function useIsDesktop(): boolean {
  const [isDesktop, setIsDesktop] = useState(false);
  useEffect(() => {
    if (typeof window === "undefined" || !window.matchMedia) return;
    const mql = window.matchMedia(DESKTOP_MQ);
    const apply = () => setIsDesktop(mql.matches);
    apply();
    mql.addEventListener("change", apply);
    return () => mql.removeEventListener("change", apply);
  }, []);
  return isDesktop;
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
