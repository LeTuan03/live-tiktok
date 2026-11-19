// Wiring tests for the auto-advancing gallery in `ProductFocusView`.
// The timer policy itself is unit-tested in
// `__tests__/feed/GalleryAutoplay.test.ts`; here we assert the React
// component drives `scrollTo` on the timer and halts on user control,
// reduced-motion, and single-image.
//
// jsdom gaps we paper over:
//   - no IntersectionObserver  → inert stub (active slide stays 0, which
//     is fine: each tick targets `(0 + 1) % n`, so we just count scrolls)
//   - no Element.scrollTo      → spy on the prototype so the gallery's
//     feature-detect passes and programmatic advances are observable
//   - no matchMedia            → absent by default (autoplay enabled);
//     stubbed only in the reduced-motion case

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import { ProductFocusView } from "./ProductFocusView";
import type { PinnedProduct } from "@/components/live/types";

function makeProduct(imageCount: number, productId = "prod-1"): PinnedProduct {
  return {
    productId,
    title: "Test Product",
    media: Array.from({ length: imageCount }, (_, i) => ({
      position: i,
      kind: "image" as const,
      url: `https://img.test/${productId}-${i}.jpg`,
    })),
    priceCents: 1999,
    currency: "USD",
    pinnedAt: 0,
  };
}

beforeAll(() => {
  if (typeof globalThis.IntersectionObserver === "undefined") {
    class StubIO {
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return [];
      }
    }
    (globalThis as { IntersectionObserver: typeof IntersectionObserver }).IntersectionObserver =
      StubIO as unknown as typeof IntersectionObserver;
  }
});

describe("ProductFocusView — gallery autoplay", () => {
  let scrollSpy: ReturnType<typeof vi.fn>;
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    vi.useFakeTimers();
    scrollSpy = vi.fn();
    Object.defineProperty(Element.prototype, "scrollTo", {
      value: scrollSpy,
      writable: true,
      configurable: true,
    });
  });

  afterEach(() => {
    vi.useRealTimers();
    // @ts-expect-error — remove the prototype stub between tests
    delete Element.prototype.scrollTo;
    if (originalMatchMedia) window.matchMedia = originalMatchMedia;
    else {
      // @ts-expect-error — jsdom ships without matchMedia; clear our stub
      delete window.matchMedia;
    }
  });

  function stubReducedMotion(reduce: boolean) {
    window.matchMedia = vi.fn().mockImplementation((query: string) => ({
      matches: reduce && query.includes("reduce"),
      media: query,
      onchange: null,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      dispatchEvent: vi.fn(),
    })) as unknown as typeof window.matchMedia;
  }

  it("advances to the next image after the 5s dwell", () => {
    render(<ProductFocusView product={makeProduct(3)} onClose={vi.fn()} />);
    scrollSpy.mockClear(); // discard the mount reset's scrollTo({left:0})

    act(() => vi.advanceTimersByTime(4999));
    expect(scrollSpy).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(scrollSpy).toHaveBeenCalledTimes(1);
  });

  it("loops — keeps advancing every interval", () => {
    render(<ProductFocusView product={makeProduct(3)} onClose={vi.fn()} />);
    scrollSpy.mockClear();

    act(() => vi.advanceTimersByTime(15_000));
    expect(scrollSpy).toHaveBeenCalledTimes(3);
  });

  it("a swipe (touchmove) on the gallery stops autoplay for good", () => {
    render(<ProductFocusView product={makeProduct(3)} onClose={vi.fn()} />);
    const img = screen.getByAltText("Test Product, image 1");
    scrollSpy.mockClear();

    fireEvent.touchMove(img);
    act(() => vi.advanceTimersByTime(30_000));
    expect(scrollSpy).not.toHaveBeenCalled();
  });

  it("a wheel/trackpad scroll stops autoplay for good", () => {
    render(<ProductFocusView product={makeProduct(3)} onClose={vi.fn()} />);
    const img = screen.getByAltText("Test Product, image 1");
    scrollSpy.mockClear();

    fireEvent.wheel(img);
    act(() => vi.advanceTimersByTime(30_000));
    expect(scrollSpy).not.toHaveBeenCalled();
  });

  it("tapping a pagination dot stops autoplay for good", () => {
    render(<ProductFocusView product={makeProduct(3)} onClose={vi.fn()} />);
    const dot = screen.getByRole("tab", { name: "Go to slide 2 of 3" });

    fireEvent.click(dot); // user-driven scroll
    scrollSpy.mockClear();
    act(() => vi.advanceTimersByTime(30_000));
    expect(scrollSpy).not.toHaveBeenCalled();
  });

  it("does not autoplay a single-image product", () => {
    render(<ProductFocusView product={makeProduct(1)} onClose={vi.fn()} />);
    scrollSpy.mockClear();

    act(() => vi.advanceTimersByTime(30_000));
    expect(scrollSpy).not.toHaveBeenCalled();
  });

  it("does not autoplay when prefers-reduced-motion is set", () => {
    stubReducedMotion(true);
    render(<ProductFocusView product={makeProduct(3)} onClose={vi.fn()} />);
    scrollSpy.mockClear();

    act(() => vi.advanceTimersByTime(30_000));
    expect(scrollSpy).not.toHaveBeenCalled();
  });
});
