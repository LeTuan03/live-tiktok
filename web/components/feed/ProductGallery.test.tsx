// Wiring tests for the auto-advancing gallery in `ProductGallery` (the
// `PersonaCard` product-focus surface). Mirrors
// `ProductFocusView.test.tsx`, plus the beacon-suppression contract that
// is unique to this component: autoplay-driven page changes must NOT fire
// `onPageChange`, because the feed-level `AutoscrollPolicy` counts that as
// a user "Rule-2 interaction".
//
// Unlike the ProductFocusView suite, this one uses a *controllable*
// IntersectionObserver stub so a test can simulate a slide settling into
// view (which is what flips `activeIndex` and would otherwise fire the
// beacon).

import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

import { ProductGallery } from "./ProductGallery";
import type { FeedSlotProduct } from "@/lib/feed/FeedService";

function makeProduct(imageCount: number, id = "slot-1"): FeedSlotProduct {
  return {
    id,
    sku: "SKU-1",
    title: "Test Product",
    media: Array.from({ length: imageCount }, (_, i) => ({
      position: i,
      kind: "image" as const,
      url: `https://img.test/${id}-${i}.jpg`,
    })),
  };
}

// Captures the latest gallery's IO callback so a test can drive it.
let lastIOCallback: IntersectionObserverCallback | null = null;

beforeAll(() => {
  class ControllableIO {
    constructor(cb: IntersectionObserverCallback) {
      lastIOCallback = cb;
    }
    observe() {}
    unobserve() {}
    disconnect() {}
    takeRecords() {
      return [];
    }
  }
  (globalThis as { IntersectionObserver: typeof IntersectionObserver }).IntersectionObserver =
    ControllableIO as unknown as typeof IntersectionObserver;
});

/** Simulate the browser settling the scroll onto slide `index` — the IO
 *  reports it ≥55% visible, which is what the component keys on. */
function settleOn(container: HTMLElement, index: number) {
  const target = container.querySelector(`[data-slide-index="${index}"]`);
  if (!target) throw new Error(`no slide ${index}`);
  act(() => {
    lastIOCallback?.(
      [{ intersectionRatio: 1, target } as unknown as IntersectionObserverEntry],
      {} as IntersectionObserver,
    );
  });
}

describe("ProductGallery — gallery autoplay", () => {
  let scrollSpy: ReturnType<typeof vi.fn>;
  const originalMatchMedia = window.matchMedia;

  beforeEach(() => {
    vi.useFakeTimers();
    lastIOCallback = null;
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
    render(<ProductGallery product={makeProduct(3)} />);
    scrollSpy.mockClear();

    act(() => vi.advanceTimersByTime(4999));
    expect(scrollSpy).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1));
    expect(scrollSpy).toHaveBeenCalledTimes(1);
  });

  it("a swipe (touchmove) stops autoplay for good", () => {
    render(<ProductGallery product={makeProduct(3)} />);
    const img = screen.getByAltText("Test Product, image 1");
    scrollSpy.mockClear();

    fireEvent.touchMove(img);
    act(() => vi.advanceTimersByTime(30_000));
    expect(scrollSpy).not.toHaveBeenCalled();
  });

  it("tapping a pagination dot stops autoplay for good", () => {
    render(<ProductGallery product={makeProduct(3)} />);
    const dot = screen.getByRole("button", { name: "Go to image 2" });

    fireEvent.click(dot);
    scrollSpy.mockClear();
    act(() => vi.advanceTimersByTime(30_000));
    expect(scrollSpy).not.toHaveBeenCalled();
  });

  it("does not autoplay a single-image product", () => {
    render(<ProductGallery product={makeProduct(1)} />);
    scrollSpy.mockClear();

    act(() => vi.advanceTimersByTime(30_000));
    expect(scrollSpy).not.toHaveBeenCalled();
  });

  it("does not autoplay when prefers-reduced-motion is set", () => {
    stubReducedMotion(true);
    render(<ProductGallery product={makeProduct(3)} />);
    scrollSpy.mockClear();

    act(() => vi.advanceTimersByTime(30_000));
    expect(scrollSpy).not.toHaveBeenCalled();
  });

  it("autoplay page changes do NOT fire onPageChange; user swipes do", () => {
    const onPageChange = vi.fn();
    const { container } = render(
      <ProductGallery product={makeProduct(3)} onPageChange={onPageChange} />,
    );
    onPageChange.mockClear(); // discard the mount-time onPageChange(0)

    // Autoplay ticks, scrolls toward slide 1, then the browser settles
    // the IO there — this must be treated as machine-driven, not a tap.
    act(() => vi.advanceTimersByTime(5000));
    settleOn(container, 1);
    expect(onPageChange).not.toHaveBeenCalled();

    // The user now swipes to slide 2: the gesture stops autoplay and the
    // settle reports through as a real interaction.
    fireEvent.touchMove(screen.getByAltText("Test Product, image 2"));
    settleOn(container, 2);
    expect(onPageChange).toHaveBeenCalledTimes(1);
    expect(onPageChange).toHaveBeenCalledWith(2);
  });
});
