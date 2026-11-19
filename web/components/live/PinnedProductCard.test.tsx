// The pinned card is one shape: a static "big" card that sits in the
// avatar-mode surface, holds its position across the entire pitch, and
// swaps content inline when the agent fires the next ad. Tapping the
// body always hands off to the caller's `onExpand` (which the FeedPager
// uses to swap the cell into product-focus mode).

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { PinnedProductCard } from "./PinnedProductCard";
import type { PinnedProduct } from "./types";

function makeProduct(overrides: Partial<PinnedProduct> = {}): PinnedProduct {
  return {
    productId: "prod-1",
    title: "Test Earbuds",
    imageUrl: "https://example.test/hero.jpg",
    priceCents: 4_900,
    currency: "USD",
    ctaLabel: "Shop now",
    pinnedAt: 0,
    ...overrides,
  };
}

describe("PinnedProductCard", () => {
  it("renders the View-X tap target", () => {
    render(<PinnedProductCard product={makeProduct()} onExpand={() => {}} />);
    expect(screen.getByLabelText(/View Test Earbuds/)).toBeInTheDocument();
  });

  it("tapping the body fires onExpand (product-focus swap)", () => {
    const onExpand = vi.fn();
    render(<PinnedProductCard product={makeProduct()} onExpand={onExpand} />);
    fireEvent.click(screen.getByLabelText(/View Test Earbuds/));
    expect(onExpand).toHaveBeenCalledTimes(1);
  });

  it("swaps content inline when the product prop changes", () => {
    const { rerender } = render(<PinnedProductCard product={makeProduct()} onExpand={() => {}} />);
    expect(screen.getByLabelText(/View Test Earbuds/)).toBeInTheDocument();
    rerender(
      <PinnedProductCard
        product={makeProduct({ productId: "prod-2", title: "Test Serum" })}
        onExpand={() => {}}
      />,
    );
    expect(screen.getByLabelText(/View Test Serum/)).toBeInTheDocument();
    expect(screen.queryByLabelText(/View Test Earbuds/)).toBeNull();
  });

  it("shows a status toast after a simulated purchase", () => {
    render(<PinnedProductCard product={makeProduct()} onExpand={() => {}} />);
    fireEvent.click(screen.getByLabelText(/Buy Test Earbuds/));
    expect(screen.getByRole("status")).toBeInTheDocument();
  });

  it("clears the purchase toast when a new product rotates in", () => {
    const { rerender } = render(<PinnedProductCard product={makeProduct()} onExpand={() => {}} />);
    fireEvent.click(screen.getByLabelText(/Buy Test Earbuds/));
    expect(screen.getByRole("status")).toBeInTheDocument();
    rerender(
      <PinnedProductCard
        product={makeProduct({ productId: "prod-2", title: "Test Serum" })}
        onExpand={() => {}}
      />,
    );
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("body is non-interactive when onExpand is omitted", () => {
    render(<PinnedProductCard product={makeProduct()} />);
    expect(screen.queryByLabelText(/View Test Earbuds/)).toBeNull();
  });
});
