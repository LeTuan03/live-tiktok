/**
 * `<GatedAction>` — pass-through wrapper.
 *
 * The sign-in modal flow is currently disabled site-wide; every
 * wrapped action fires its callback on click regardless of auth state.
 * These tests pin that behavior so anyone restoring the modal split
 * has to update the suite explicitly.
 */
import { afterEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";

const { GatedAction } = await import("@/components/auth/GatedAction");

afterEach(() => {
  vi.restoreAllMocks();
});

describe("GatedAction — pass-through", () => {
  it("clicking the wrapped child invokes the action callback", () => {
    const onAction = vi.fn();
    render(
      <GatedAction onAction={onAction}>
        <button type="button">Favorite</button>
      </GatedAction>,
    );
    fireEvent.click(screen.getByRole("button", { name: /favorite/i }));
    expect(onAction).toHaveBeenCalledTimes(1);
  });

  it("preserves the child element so the visual affordance is identical", () => {
    render(
      <GatedAction onAction={() => {}}>
        <button type="button" aria-label="Heart" className="brand-affordance">
          ❤️
        </button>
      </GatedAction>,
    );
    const button = screen.getByRole("button", { name: /heart/i });
    expect(button).toHaveClass("brand-affordance");
    expect(button.textContent).toBe("❤️");
  });
});

describe("GatedAction — child constraint", () => {
  it("requires exactly one child (Children.only)", () => {
    const consoleError = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() =>
      render(
        // @ts-expect-error — exercising the runtime guard, not the type.
        <GatedAction onAction={() => {}}>
          <button type="button">A</button>
          <button type="button">B</button>
        </GatedAction>,
      ),
    ).toThrow();
    consoleError.mockRestore();
  });
});
