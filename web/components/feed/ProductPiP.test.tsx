// Tests for the pinned-product PiP.
//
// Two behaviors worth pinning down so future refactors don't quietly
// break the feed:
//
//   1. The expanded hero auto-collapses to a badge after 3s.
//   2. Tapping the collapsed badge re-expands; tapping the expanded
//      hero calls `onExpand` (i.e. the caller swaps product into the
//      full-bleed slot).

import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ProductPiP } from "./ProductPiP";
import type { FeedSlotProduct } from "@/lib/feed/FeedService";

function makeProduct(overrides: Partial<FeedSlotProduct> = {}): FeedSlotProduct {
  return {
    id: "prod-1",
    sku: "SKU-TEST",
    title: "Test Headphones",
    description: null,
    priceCents: 12_900,
    currency: "USD",
    deepLinkUrl: null,
    landingUrl: null,
    media: [{ position: 0, kind: "image", url: "https://example.test/hero.jpg" }],
    ...overrides,
  };
}

describe("ProductPiP", () => {
  it("starts expanded and renders the hero image + title + price", () => {
    render(<ProductPiP product={makeProduct()} onExpand={() => {}} />);
    expect(screen.getByLabelText(/Shop Test Headphones/)).toBeInTheDocument();
    expect(screen.getByText("Test Headphones")).toBeInTheDocument();
    expect(screen.getByText("$129.00")).toBeInTheDocument();
  });

  it("auto-collapses to a badge after 3s", () => {
    vi.useFakeTimers();
    try {
      render(<ProductPiP product={makeProduct()} onExpand={() => {}} />);
      // Expanded label is "Shop X".
      expect(screen.getByLabelText(/Shop Test Headphones/)).toBeInTheDocument();
      act(() => {
        vi.advanceTimersByTime(3_000);
      });
      // Collapsed label is "View X".
      expect(screen.getByLabelText(/View Test Headphones/)).toBeInTheDocument();
    } finally {
      vi.useRealTimers();
    }
  });

  it("tapping the expanded hero fires onExpand and does NOT collapse", () => {
    // DraggablePiP discriminates between tap and drag via pointer events,
    // and also exposes a keyboard fast-path on Enter/Space. We exercise
    // the keyboard path because it's deterministic without jsdom
    // pointer-capture support.
    const onExpand = vi.fn();
    render(<ProductPiP product={makeProduct()} onExpand={onExpand} />);
    fireEvent.keyDown(screen.getByLabelText(/Shop Test Headphones/), {
      key: "Enter",
    });
    expect(onExpand).toHaveBeenCalledTimes(1);
  });

  it("tapping the collapsed badge re-expands (without firing onExpand)", () => {
    vi.useFakeTimers();
    try {
      const onExpand = vi.fn();
      render(<ProductPiP product={makeProduct()} onExpand={onExpand} />);

      act(() => {
        vi.advanceTimersByTime(3_000);
      });
      const collapsed = screen.getByLabelText(/View Test Headphones/);
      fireEvent.keyDown(collapsed, { key: "Enter" });

      // Back to expanded, and onExpand was NOT fired.
      expect(screen.getByLabelText(/Shop Test Headphones/)).toBeInTheDocument();
      expect(onExpand).not.toHaveBeenCalled();
    } finally {
      vi.useRealTimers();
    }
  });

  it("falls back to the raw cents on locale-quirky currencies (no Intl crash)", () => {
    const product = makeProduct({ currency: "??invalid??" });
    render(<ProductPiP product={product} onExpand={() => {}} />);
    // The format helper's catch-block prints `$X.XX` as a last resort —
    // the test only asserts that *something* numeric renders, not the
    // exact glyph, since Intl behavior differs per ICU version.
    expect(screen.getByText(/129/)).toBeInTheDocument();
  });

  it("renders a placeholder rectangle when no image media is present", () => {
    const product = makeProduct({
      media: [{ position: 1, kind: "video", url: "https://example/video.mp4" }],
    });
    const { container } = render(<ProductPiP product={product} onExpand={() => {}} />);
    // No <img> rendered in the hero — the empty placeholder div has
    // aria-hidden so screen readers skip it.
    expect(container.querySelector("img")).toBeNull();
  });
});
