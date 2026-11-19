"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { GalleryAutoplay } from "@/lib/feed/GalleryAutoplay";
import { usePrefersReducedMotion } from "@/lib/feed/usePrefersReducedMotion";
import type { FeedSlotProduct, FeedSlotProductMedia } from "@/lib/feed/FeedService";

interface ProductGalleryProps {
  product: FeedSlotProduct;
  /** Notified when the user pages between media items so the autoscroll
   *  policy can count it as a Rule-2 interaction. */
  onPageChange?: (index: number) => void;
}

/**
 * Horizontal-swipe gallery for a product's ordered media list.
 *
 * Conventions match `server/migrations/0001_initial_schema.up.sql`:
 *
 *   - position 0 = feature image
 *   - position 1 = feature video (when present)
 *   - position 2+ = additional gallery images
 *
 * The container is `scroll-snap-x mandatory` so a swipe always lands on
 * a whole media slot; `IntersectionObserver` picks the most-visible
 * slide for the pagination indicator without coupling to the scroll
 * handler. Falls back to a single hero render when the product has no
 * `media` array yet (legacy catalog rows).
 */
export function ProductGallery({ product, onPageChange }: ProductGalleryProps) {
  const items = resolveMedia(product);

  const scrollerRef = useRef<HTMLDivElement | null>(null);
  const slideRefs = useRef<Map<number, HTMLElement>>(new Map());
  const [activeIndex, setActiveIndex] = useState(0);

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

  // Index the autoplay timer last scrolled to. The IO settles `activeIndex`
  // to this value a beat later; that settle is NOT a user interaction, so
  // it must not fire the `onPageChange` beacon (which the feed-level
  // `AutoscrollPolicy` counts as a Rule-2 interaction). A genuine swipe or
  // dot tap lands on a different index — or clears this ref — so it still
  // reports through.
  const autoplayTargetRef = useRef<number | null>(null);

  useEffect(() => {
    if (autoplayTargetRef.current === activeIndex) {
      // Autoplay drove this page change — swallow the beacon, then forget
      // the target so the next genuine change reports normally.
      autoplayTargetRef.current = null;
      return;
    }
    autoplayTargetRef.current = null;
    onPageChange?.(activeIndex);
  }, [activeIndex, onPageChange]);

  const setSlideRef = (index: number) => (el: HTMLElement | null) => {
    if (el) slideRefs.current.set(index, el);
    else slideRefs.current.delete(index);
  };

  const scrollTo = (index: number) => {
    const target = slideRefs.current.get(index);
    if (!target || !scrollerRef.current) return;
    scrollerRef.current.scrollTo({
      left: target.offsetLeft,
      behavior: "smooth",
    });
  };

  // ── Auto-advancing gallery ────────────────────────────────────────────
  // While the product is full-bleed and the seller is in PiP (the only
  // state in which `PersonaCard` mounts this), photos paginate on a 5s
  // timer so the viewer sees the whole set hands-free. The first manual
  // gesture — swipe/scroll, dot — stops it for good for this product;
  // hover and tab-hidden pause it temporarily. Mirrors `ProductFocusView`;
  // see `GalleryAutoplay` for the spec. Skipped under reduced-motion or
  // with a single image.
  const prefersReducedMotion = usePrefersReducedMotion();
  const activeIndexRef = useRef(0);
  activeIndexRef.current = activeIndex;
  const scrollToRef = useRef(scrollTo);
  scrollToRef.current = scrollTo;
  const autoplayRef = useRef<GalleryAutoplay | null>(null);

  useEffect(() => {
    if (items.length <= 1 || prefersReducedMotion) return;
    const autoplay = new GalleryAutoplay({
      advance: () => {
        const next = (activeIndexRef.current + 1) % items.length;
        // Flag the upcoming IO settle as autoplay-driven (see effect above).
        autoplayTargetRef.current = next;
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
  }, [product.id, items.length, prefersReducedMotion]);

  // Permanent stop once the user drives the gallery themselves.
  const stopAutoplay = useCallback(() => autoplayRef.current?.stop(), []);

  return (
    <div
      className="absolute inset-0 bg-black"
      // Mouse hover pauses autoplay (resumable); leave resumes. Touch has
      // no hover, so this is desktop-only by nature.
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
        // taking control — stop autoplay for good. We deliberately do NOT
        // listen to `scroll`, which also fires for our own programmatic
        // `scrollTo` and would self-cancel the timer.
        onTouchMove={stopAutoplay}
        onWheel={stopAutoplay}
      >
        {items.map((item, idx) => (
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
        ))}
      </div>

      {items.length > 1 && (
        <div className="pointer-events-auto absolute inset-x-0 bottom-3 z-10 flex items-center justify-center gap-1.5">
          {items.map((_, idx) => (
            <button
              key={idx}
              type="button"
              aria-label={`Go to image ${idx + 1}`}
              aria-current={idx === activeIndex ? "true" : undefined}
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
  );
}

/** Materialise the renderable list. The server returns `media` already
 *  sorted by position; we treat it as the single source of truth and
 *  filter to image entries (legacy DB rows may still carry the retired
 *  placeholder video at position 1). */
function resolveMedia(product: FeedSlotProduct): FeedSlotProductMedia[] {
  return (product.media ?? []).filter((m) => m.kind === "image");
}
