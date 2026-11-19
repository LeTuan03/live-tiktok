import { describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { ToastProvider, useToast } from "./toast";

function ShowButton({ kind = "info" as const }: { kind?: "info" | "success" | "error" }) {
  const { show } = useToast();
  return (
    <button type="button" onClick={() => show({ kind, title: `Hello ${kind}`, body: "context" })}>
      show
    </button>
  );
}

describe("ToastProvider", () => {
  it("renders a toast when show() is called and dismisses after the duration", () => {
    vi.useFakeTimers();
    try {
      render(
        <ToastProvider>
          <ShowButton kind="success" />
        </ToastProvider>,
      );

      act(() => {
        fireEvent.click(screen.getByText("show"));
      });
      expect(screen.getByTestId("Toast.success")).toHaveTextContent("Hello success");
      expect(screen.getByTestId("Toast.success")).toHaveTextContent("context");

      act(() => {
        vi.advanceTimersByTime(4_000);
      });
      expect(screen.queryByTestId("Toast.success")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("dismisses on tap before the auto-dismiss fires", () => {
    render(
      <ToastProvider>
        <ShowButton kind="error" />
      </ToastProvider>,
    );

    fireEvent.click(screen.getByText("show"));
    const toast = screen.getByTestId("Toast.error");
    fireEvent.click(toast);
    expect(screen.queryByTestId("Toast.error")).toBeNull();
  });

  it("throws when useToast is called outside a provider", () => {
    function Naked() {
      useToast();
      return null;
    }
    // RTL surfaces the React error via console.error; assert by render.
    expect(() => render(<Naked />)).toThrow(/useToast\(\) called outside/);
  });
});
