"use client";

// Full-bleed in-app product detail surface, shown when the user taps
// the pinned product pitch on the active feed cell. Replaces the
// previous external `<a href={landingUrl}>` so SoShop never
// navigates the user out of the app — purchase happens in-app via
// the `Buy` button.
//
// The visual frame is a paginated horizontal media gallery filling
// the cell, with a floating action card at the bottom (title +
// price + Buy CTA). The seller avatar PiP is mounted by `FeedPager`
// as a sibling overlay so audio + video stay bound while the user
// browses photos and videos — see `FeedPager.tsx` for that wiring.

import { useCallback, useEffect, useRef, useState } from "react";
import { ChevronLeft, ChevronRight, X } from "lucide-react";
import { GatedAction } from "@/components/auth/GatedAction";
import { GalleryAutoplay } from "@/lib/feed/GalleryAutoplay";
import { usePrefersReducedMotion } from "@/lib/feed/usePrefersReducedMotion";
import type { PinnedProduct, PinnedProductMedia } from "@/components/live/types";

interface ProductFocusViewProps {
  product: PinnedProduct;
  /** Dismiss the focus view and return the seller to the full-bleed
   *  slot. Bound to the close button in the top-left and to the
   *  seller PiP's tap target in the caller. */
  onClose: () => void;
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

/** Resolve the renderable gallery. Filters out non-image entries since
 *  the catalog only ships product photography post-IS-tba (the legacy
 *  placeholder video slot was retired). Falls back to a single-slide
 *  list built from `imageUrl` when the product has no `media` array —
 *  so legacy catalog rows without `product_media` still render
 *  *something* rather than going blank when the user expands them. */
function resolveMedia(product: PinnedProduct): PinnedProductMedia[] {
  const fromMedia = (product.media ?? []).filter((m) => m.kind === "image");
  if (fromMedia.length > 0) return fromMedia;
  if (product.imageUrl) {
    return [{ position: 0, kind: "image", url: product.imageUrl }];
  }
  return [];
}

export function ProductFocusView({ product, onClose }: ProductFocusViewProps) {
  const [purchaseConfirmation, setPurchaseConfirmation] = useState<string | null>(null);

  const price =
    typeof product.priceCents === "number" && product.currency
      ? formatPrice(product.priceCents, product.currency)
      : null;
  const ctaButtonLabel = product.ctaLabel ?? "Buy now";

  function handleBuy() {
    const priceCopy = price ? ` for ${price}` : "";
    setPurchaseConfirmation(`Purchase confirmed: ${product.title}${priceCopy}.`);
  }

  const items = resolveMedia(product);

  // Active pagination index for the dot indicator. `IntersectionObserver`
  // picks the most-visible slide so the dots stay in sync with both
  // touch swipes and the programmatic `scrollTo` we wire to dot taps.
  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const slideRefs = useRef<Map<number, HTMLElement>>(new Map());
  const [activeIndex, setActiveIndex] = useState(0);

  // Reset per-product UI when a fresh product pins (e.g. agent rotated
  // to the next ad, or the user closed and re-opened focus mode after a
  // swap). Both the gallery scroll position and the post-purchase
  // confirmation belong to the previous product — leaving the
  // confirmation up would falsely claim the new product was purchased.
  // `scrollTo` is feature-detected because jsdom (Vitest) doesn't
  // implement it on `Element`.
  useEffect(() => {
    setActiveIndex(0);
    setPurchaseConfirmation(null);
    const scroller = scrollerRef.current;
    if (scroller && typeof scroller.scrollTo === "function") {
      scroller.scrollTo({ left: 0 });
    }
  }, [product.productId]);

  useEffect(() => {
    if (items.length <= 1) return;
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const observer = new IntersectionObserver(
      (entries) => {
        let best: { idx: number; ratio: number } | null = null;
        for (const entry of entries) {
          if (entry.intersectionRatio < 0.55) continue;
          const idxAttr = (entry.target as HTMLElement).dataset.slideIndex;
          if (!idxAttr) continue;
          const idx = Number(idxAttr);
          if (!best || entry.intersectionRatio > best.ratio) {
            best = { idx, ratio: entry.intersectionRatio };
          }
        }
        if (best) setActiveIndex(best.idx);
      },
      { root: scroller, threshold: [0.55, 0.85, 1] },
    );
    for (const el of slideRefs.current.values()) observer.observe(el);
    return () => observer.disconnect();
  }, [items.length]);

  const setSlideRef = (index: number) => (el: HTMLElement | null) => {
    if (el) slideRefs.current.set(index, el);
    else slideRefs.current.delete(index);
  };

  const scrollTo = (index: number) => {
    const target = slideRefs.current.get(index);
    const scroller = scrollerRef.current;
    if (!target || !scroller || typeof scroller.scrollTo !== "function") return;
    scroller.scrollTo({
      left: target.offsetLeft,
      behavior: "smooth",
    });
  };

  // ── Auto-advancing gallery ────────────────────────────────────────────
  // While the product is full-bleed and the seller is in PiP (the only
  // state in which this view is mounted), the photos paginate on a 5s
  // timer so the viewer sees the whole set hands-free. The first manual
  // gesture — a swipe/scroll, a dot, or a chevron — stops it for good for
  // this product; hover and tab-hidden pause it temporarily. See
  // `GalleryAutoplay` for the full spec. Skipped under reduced-motion or
  // with a single image.
  const prefersReducedMotion = usePrefersReducedMotion();
  const activeIndexRef = useRef(0);
  activeIndexRef.current = activeIndex;
  // `scrollTo` is recreated each render (closes over refs); the timer
  // calls the latest copy through a ref so it never scrolls a stale node.
  const scrollToRef = useRef(scrollTo);
  scrollToRef.current = scrollTo;
  const autoplayRef = useRef<GalleryAutoplay | null>(null);

  useEffect(() => {
    if (items.length <= 1 || prefersReducedMotion) return;
    const autoplay = new GalleryAutoplay({
      advance: () => {
        const next = (activeIndexRef.current + 1) % items.length;
        scrollToRef.current(next);
      },
    });
    autoplayRef.current = autoplay;
    autoplay.start();

    const onVisibility = () => autoplay.setHidden(document.visibilityState === "hidden");
    onVisibility();
    document.addEventListener("visibilitychange", onVisibility);

    return () => {
      document.removeEventListener("visibilitychange", onVisibility);
      autoplay.dispose();
      autoplayRef.current = null;
    };
    // Rebuild per product (a fresh pin re-arms autoplay) and when the
    // image count or motion preference changes.
  }, [product.productId, items.length, prefersReducedMotion]);

  // Permanent stop once the user drives the gallery themselves.
  const stopAutoplay = useCallback(() => autoplayRef.current?.stop(), []);

  return (
    <div className="absolute inset-0 z-30 flex flex-col bg-black text-white">
      {/* Full-bleed paginated media gallery. Pagination dots float over
          the bottom edge above the action card; the seller PiP is
          mounted by the caller and snaps to bottom-right by default. */}
      <div
        className="relative flex-1 overflow-hidden bg-black"
        // Mouse hover pauses autoplay (resumable); pointer-leave resumes.
        // Touch has no hover, so this is desktop-only by nature.
        onMouseEnter={() => autoplayRef.current?.setHovered(true)}
        onMouseLeave={() => autoplayRef.current?.setHovered(false)}
      >
        <div
          ref={scrollerRef}
          className="scrollbar-none flex h-full w-full overflow-x-scroll overflow-y-hidden"
          style={{
            scrollSnapType: "x mandatory",
            scrollBehavior: "smooth",
            WebkitOverflowScrolling: "touch",
            overscrollBehaviorX: "contain",
          }}
          // A real swipe (touchmove) or trackpad/wheel scroll is the user
          // taking control — stop autoplay permanently. We deliberately do
          // NOT listen to `scroll`, which also fires for our own
          // programmatic `scrollTo` and would self-cancel the timer.
          onTouchMove={stopAutoplay}
          onWheel={stopAutoplay}
        >
          {items.length === 0 ? (
            <div className="relative h-full w-full flex-shrink-0 bg-neutral-900" aria-hidden />
          ) : (
            items.map((item, idx) => (
              <div
                key={`${item.position}-${item.url}`}
                ref={setSlideRef(idx)}
                data-slide-index={idx}
                className="relative h-full w-full flex-shrink-0 snap-center snap-always"
              >
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img
                  src={item.url}
                  alt={`${product.title}, image ${idx + 1}`}
                  className="h-full w-full object-contain"
                  loading="eager"
                  decoding="async"
                />
              </div>
            ))
          )}
        </div>

        {/* Close target. Sized at the HIG floor (44pt) on mobile and 48pt
            on desktop so the hit area stays comfortable on both pointer
            and touch. Lifted to `top-16` on mobile to clear the fixed
            `SoShop` wordmark + `Live · MM:SS` chip in the top chrome —
            on desktop the gallery column is letterboxed away from the
            header, so the X can stay at `top-4`. The container holds
            the hit area; the inner span paints the visible glyph circle
            so the icon doesn't grow to the full 44–48pt diameter. */}
        <button
          type="button"
          onClick={onClose}
          aria-label="Close product details"
          className="absolute left-3 top-16 z-10 flex h-11 w-11 items-center justify-center text-white transition active:scale-95 md:left-4 md:top-4 md:h-12 md:w-12"
        >
          <span className="flex h-9 w-9 items-center justify-center rounded-full bg-black/55 backdrop-blur md:h-10 md:w-10">
            <X size={20} strokeWidth={2.2} />
          </span>
        </button>

        {/* Desktop chevron buttons. Mobile relies on the native horizontal
            swipe; pointer-input desktops get explicit prev/next controls
            so the user can page without a touchscreen. Hidden under `md:`
            so the chevrons never overlay the mobile swipe target. */}
        {items.length > 1 && (
          <>
            <button
              type="button"
              onClick={() => {
                stopAutoplay();
                scrollTo(Math.max(0, activeIndex - 1));
              }}
              disabled={activeIndex === 0}
              aria-label="Previous image"
              className="absolute left-4 top-1/2 z-10 hidden h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full bg-black/55 text-white backdrop-blur transition hover:bg-black/70 active:scale-95 disabled:cursor-not-allowed disabled:opacity-30 md:flex"
            >
              <ChevronLeft size={22} strokeWidth={2.2} />
            </button>
            <button
              type="button"
              onClick={() => {
                stopAutoplay();
                scrollTo(Math.min(items.length - 1, activeIndex + 1));
              }}
              disabled={activeIndex === items.length - 1}
              aria-label="Next image"
              className="absolute right-4 top-1/2 z-10 hidden h-10 w-10 -translate-y-1/2 items-center justify-center rounded-full bg-black/55 text-white backdrop-blur transition hover:bg-black/70 active:scale-95 disabled:cursor-not-allowed disabled:opacity-30 md:flex"
            >
              <ChevronRight size={22} strokeWidth={2.2} />
            </button>
          </>
        )}

        {items.length > 1 && (
          <div
            role="tablist"
            aria-label="Product media pagination"
            className="pointer-events-auto absolute inset-x-0 bottom-3 z-10 flex items-center justify-center gap-1.5"
          >
            {items.map((_, idx) => (
              <button
                key={idx}
                type="button"
                role="tab"
                aria-label={`Go to slide ${idx + 1} of ${items.length}`}
                aria-selected={idx === activeIndex}
                onClick={() => {
                  stopAutoplay();
                  scrollTo(idx);
                }}
                className={`h-1.5 rounded-full transition-all ${
                  idx === activeIndex ? "w-6 bg-white" : "w-1.5 bg-white/45 hover:bg-white/70"
                }`}
              />
            ))}
          </div>
        )}
      </div>

      {/* Action card. Sized to leave the gallery the lion's share of the
          viewport; the seller PiP defaults to bottom-right just above
          this card so neither covers the product photography. */}
      <div className="flex flex-shrink-0 flex-col gap-3 px-5 pb-5 pt-4">
        <div>
          <h2 className="text-xl font-semibold leading-tight">{product.title}</h2>
          {(price || product.cta) && (
            <div className="mt-1 text-base font-medium text-white/85">{price ?? product.cta}</div>
          )}
        </div>

        {purchaseConfirmation ? (
          <div
            role="status"
            aria-live="polite"
            className="rounded-lg bg-emerald-500/15 px-3 py-2 text-sm font-medium text-emerald-200 ring-1 ring-emerald-400/30"
          >
            {purchaseConfirmation}
          </div>
        ) : (
          <GatedAction onAction={handleBuy}>
            <button
              type="button"
              aria-label={`Buy ${product.title}`}
              className="rounded-full bg-brand px-5 py-3 text-sm font-semibold text-white transition active:scale-[0.98]"
            >
              {ctaButtonLabel}
            </button>
          </GatedAction>
        )}
      </div>
    </div>
  );
}
