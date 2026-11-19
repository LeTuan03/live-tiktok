/**
 * Navigation coordinator's modal-cover semantics.
 *
 * Hermetic. Asserts the contract that:
 *   1. The intercepting route (`@modal/(.)persona`) wraps its content in
 *      `<ModalCover>`, so tapping an entry from `/` mounts a modal overlay.
 *   2. The cold-load route (`/persona`) does NOT wrap its content in
 *      `<ModalCover>`, so opening the URL in a new tab still works as a
 *      deep-linkable full-page surface.
 *   3. The modal's close affordance (X button + Escape key) both call
 *      `router.back()` so the user lands back on `/` (Feed).
 *
 * Next.js's `useRouter` is mocked so we can assert the back() spy was
 * called.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen } from "@testing-library/react";

const back = vi.fn();
const push = vi.fn();
const replace = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({ back, push, replace, forward: vi.fn(), refresh: vi.fn() }),
  useSearchParams: () => new URLSearchParams(),
  usePathname: () => "/",
}));

// `lucide-react` ships ESM that vite-node sometimes can't resolve in
// the test environment (no browser entry resolution). The X icon is
// purely decorative — stub it as a marker SVG so the navigation test
// doesn't need the icon library installed.
vi.mock("lucide-react", () => ({
  X: () => <span data-testid="x-icon" />,
}));

vi.mock("@/components/persona/PersonaSurface", () => ({
  PersonaSurface: ({ personaId }: { personaId: string }) => (
    <div data-testid="persona-surface">persona-{personaId}</div>
  ),
}));

const { ModalCover } = await import("@/components/consumer/ModalCover");
const { default: InterceptedPersonaModal } = await import(
  "@/app/(consumer)/@modal/(.)persona/page"
);
const { default: ModalDefault } = await import("@/app/(consumer)/@modal/default");
const { default: PersonaPage } = await import("@/app/(consumer)/persona/page");

beforeEach(() => {
  back.mockReset();
  push.mockReset();
  replace.mockReset();
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("Modal-cover chrome — close affordances", () => {
  it("renders a dialog with the supplied close label", () => {
    render(
      <ModalCover closeLabel="Close panel">
        <p>body</p>
      </ModalCover>,
    );
    expect(screen.getByRole("dialog")).toHaveAttribute("aria-modal", "true");
    expect(screen.getByRole("button", { name: /close panel/i })).toBeInTheDocument();
  });

  it("clicking the X button calls router.back() — exits to Feed", () => {
    render(
      <ModalCover>
        <p>body</p>
      </ModalCover>,
    );
    fireEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(back).toHaveBeenCalledTimes(1);
  });

  it("pressing Escape calls router.back() — same exit as X", () => {
    render(
      <ModalCover>
        <p>body</p>
      </ModalCover>,
    );
    act(() => {
      window.dispatchEvent(new KeyboardEvent("keydown", { key: "Escape" }));
    });
    expect(back).toHaveBeenCalledTimes(1);
  });

  it("locks body scroll while open and restores on unmount", () => {
    const previous = document.body.style.overflow;
    const { unmount } = render(
      <ModalCover>
        <p>body</p>
      </ModalCover>,
    );
    expect(document.body.style.overflow).toBe("hidden");
    unmount();
    expect(document.body.style.overflow).toBe(previous);
  });
});

describe("Intercepting route → modal-cover", () => {
  it("(.)persona wraps the Ask-Avatar surface in <ModalCover>", () => {
    render(<InterceptedPersonaModal />);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: /close askavatar/i })).toBeInTheDocument();
    expect(screen.getByTestId("persona-surface")).toBeInTheDocument();
  });

  it("@modal/default.tsx renders nothing when no intercept matches", () => {
    const { container } = render(<ModalDefault />);
    expect(container.firstChild).toBeNull();
  });
});

describe("Cold-load route → full-page (no modal-cover)", () => {
  it("/persona renders the Ask-Avatar surface WITHOUT a modal dialog", () => {
    render(<PersonaPage />);
    expect(screen.queryByRole("dialog")).toBeNull();
    expect(screen.getByTestId("persona-surface")).toBeInTheDocument();
  });
});
