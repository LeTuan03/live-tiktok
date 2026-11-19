import { StrictMode } from "react";
import { describe, expect, it, vi, beforeEach, afterEach } from "vitest";
import { act, fireEvent, render, screen, within } from "@testing-library/react";
import { GlobalChatOverlay } from "./GlobalChatOverlay";

describe("GlobalChatOverlay", () => {
  beforeEach(() => {
    vi.useFakeTimers();
  });
  afterEach(() => {
    vi.useRealTimers();
  });

  it("renders simulated comments after the script starts", () => {
    render(<GlobalChatOverlay productId="prod-render" />);

    // The first scripted tick lands inside ~1s for any product hash.
    act(() => {
      vi.advanceTimersByTime(2_000);
    });

    const stack = screen.getByLabelText("Live chat").querySelector('[role="log"]');
    if (!stack) throw new Error("chat log missing");
    expect(stack.children.length).toBeGreaterThan(0);
  });

  it("caps the visible window at 5 rows", () => {
    render(<GlobalChatOverlay productId="prod-cap" />);
    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    const stack = screen.getByLabelText("Live chat").querySelector('[role="log"]');
    if (!stack) throw new Error("chat log missing");
    expect(stack.children.length).toBeLessThanOrEqual(5);
  });

  it("submits a user comment into the rolling stream", () => {
    render(<GlobalChatOverlay productId="prod-send" selfHandle="ada" />);
    const input = screen.getByPlaceholderText("Say something…") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "hello from ada" } });
    fireEvent.submit(input.closest("form")!);
    const log = screen.getByLabelText("Live chat").querySelector('[role="log"]');
    if (!log) throw new Error("chat log missing");
    expect(within(log as HTMLElement).getByText("hello from ada")).toBeInTheDocument();
    // The composer is cleared on submit.
    expect(input.value).toBe("");
  });

  it("ignores empty submissions", () => {
    render(<GlobalChatOverlay productId="prod-empty" />);
    const input = screen.getByPlaceholderText("Say something…") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "   " } });
    fireEvent.submit(input.closest("form")!);
    const log = screen.getByLabelText("Live chat").querySelector('[role="log"]');
    if (!log) throw new Error("chat log missing");
    // Only simulator messages should be present (which, at t=0 with no
    // advance, is none).
    const selfRow = log.querySelector('[data-author-kind="self"]');
    expect(selfRow).toBeNull();
  });

  it("fans the user's message out to the LiveKit bridge when wired", () => {
    const publish = vi.fn(async () => undefined);
    const bridge = {
      subscribe: () => () => undefined,
      start: () => undefined,
      stop: () => undefined,
      publish,
    };
    render(<GlobalChatOverlay productId="prod-broadcast" selfHandle="ada" chatBridge={bridge} />);
    const input = screen.getByPlaceholderText("Say something…") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "broadcast me" } });
    fireEvent.submit(input.closest("form")!);
    expect(publish).toHaveBeenCalledWith("ada", "broadcast me");
  });

  it("renders comments under React Strict Mode (no dead-store regression)", () => {
    // Next.js has `reactStrictMode: true` so the dev runtime double-
    // invokes effects (mount → cleanup → mount). A regression here is
    // what caused the production-mode "comments totally disappear"
    // bug: a lazy-useState store was disposed in the first cleanup
    // and the second mount inherited the dead instance.
    render(
      <StrictMode>
        <GlobalChatOverlay productId="prod-strict" />
      </StrictMode>,
    );
    act(() => {
      vi.advanceTimersByTime(4_000);
    });
    const stack = screen.getByLabelText("Live chat").querySelector('[role="log"]');
    if (!stack) throw new Error("chat log missing");
    expect(stack.children.length).toBeGreaterThan(0);
  });

  it("caps the compact-density visible window at 5 rows (same crowd density as full mode)", () => {
    render(<GlobalChatOverlay productId="prod-compact-cap" density="compact" />);
    act(() => {
      vi.advanceTimersByTime(30_000);
    });
    const stack = screen.getByLabelText("Live chat").querySelector('[role="log"]');
    if (!stack) throw new Error("chat log missing");
    expect(stack.children.length).toBeLessThanOrEqual(5);
  });

  it("preserves the rolling buffer when density flips (no remount)", () => {
    const { rerender } = render(<GlobalChatOverlay productId="prod-density" density="full" />);
    act(() => {
      vi.advanceTimersByTime(4_000);
    });
    const logFull = screen.getByLabelText("Live chat").querySelector('[role="log"]');
    if (!logFull) throw new Error("chat log missing");
    const snapshotFull = Array.from(logFull.children).map((el) =>
      (el as HTMLElement).getAttribute("data-message-id"),
    );
    expect(snapshotFull.length).toBeGreaterThan(0);

    // Swap density without advancing timers — the store survives the
    // prop change, so the same rolling window must remain on screen
    // (both densities pull the same trailing slice; only the row
    // styling differs).
    rerender(<GlobalChatOverlay productId="prod-density" density="compact" />);
    const overlay = screen.getByTestId("GlobalChatOverlay");
    expect(overlay.dataset.density).toBe("compact");
    const logCompact = overlay.querySelector('[role="log"]');
    if (!logCompact) throw new Error("chat log missing");
    const heldOver = Array.from(logCompact.children).map((el) =>
      (el as HTMLElement).getAttribute("data-message-id"),
    );
    expect(heldOver).toEqual(snapshotFull);
  });

  it("keeps prior comments on screen when productId changes (continuous stream)", () => {
    const { rerender } = render(<GlobalChatOverlay productId="prod-A" />);
    // Let the first product's simulator land a handful of messages.
    act(() => {
      vi.advanceTimersByTime(4_000);
    });
    const logA = screen.getByLabelText("Live chat").querySelector('[role="log"]');
    if (!logA) throw new Error("chat log missing");
    const firstSnapshot = Array.from(logA.children).map((el) =>
      (el as HTMLElement).getAttribute("data-message-id"),
    );
    expect(firstSnapshot.length).toBeGreaterThan(0);

    // Swap to a fresh product without advancing timers — the new
    // simulator can't have emitted yet, so every visible row must be
    // a hold-over from the previous product. That's the "continuous
    // stream" guarantee the user cares about.
    rerender(<GlobalChatOverlay productId="prod-B" />);
    const logB = screen.getByLabelText("Live chat").querySelector('[role="log"]');
    if (!logB) throw new Error("chat log missing");
    const heldOver = Array.from(logB.children).map((el) =>
      (el as HTMLElement).getAttribute("data-message-id"),
    );
    expect(heldOver.length).toBe(firstSnapshot.length);
    expect(heldOver).toEqual(firstSnapshot);
  });
});
