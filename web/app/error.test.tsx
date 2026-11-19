// Tests for the App Router per-segment error boundary
// (`web/app/error.tsx`). The boundary is a thin shell:
//
//   - Renders the error's `message` (with a fallback string)
//   - Renders the `digest` (Next's anonymized error id) when present
//   - Wires the "Try again" button to the `reset` callback Next.js
//     passes in
//
// We don't try to test the boundary's invocation path (the Next.js
// runtime triggers it); we test the component in isolation as a
// presenter, the same way Next's own docs recommend.

import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import RouteError from "./error";

describe("RouteError boundary", () => {
  it("renders the error's message and the retry CTA", () => {
    render(
      <RouteError
        error={Object.assign(new Error("Channel feed unreachable"), {})}
        reset={() => {}}
      />,
    );
    expect(screen.getByRole("alert")).toBeInTheDocument();
    expect(screen.getByText("Channel feed unreachable")).toBeInTheDocument();
    expect(screen.getByTestId("RouteError.retry")).toBeInTheDocument();
  });

  it("falls back to a generic message when the error has none", () => {
    const empty = Object.assign(new Error(""), {});
    render(<RouteError error={empty} reset={() => {}} />);
    expect(screen.getByText(/Something went wrong. Try again in a moment\./)).toBeInTheDocument();
  });

  it("renders the Next.js error digest when present", () => {
    const error = Object.assign(new Error("Boom"), { digest: "abc-123" });
    render(<RouteError error={error} reset={() => {}} />);
    expect(screen.getByText(/ref: abc-123/)).toBeInTheDocument();
  });

  it("does NOT render a ref line when no digest is supplied", () => {
    render(<RouteError error={new Error("Boom")} reset={() => {}} />);
    expect(screen.queryByText(/^ref:/)).toBeNull();
  });

  it("invokes the `reset` callback when the retry button is clicked", () => {
    const reset = vi.fn();
    render(<RouteError error={new Error("Boom")} reset={reset} />);
    fireEvent.click(screen.getByTestId("RouteError.retry"));
    expect(reset).toHaveBeenCalledTimes(1);
  });

  it("logs the error via console.error so Vercel + the browser pick it up", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    try {
      const error = new Error("Diagnostic");
      render(<RouteError error={error} reset={() => {}} />);
      expect(spy).toHaveBeenCalled();
      // Ensure the error object itself flows through to the log (not just a
      // message string) so the stack and digest stay available to any error
      // reporter wired in later.
      const callArgs = spy.mock.calls.at(0);
      expect(callArgs).toBeDefined();
      expect(callArgs?.some((arg) => arg === error)).toBe(true);
    } finally {
      spy.mockRestore();
    }
  });
});
