import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { BootstrapOverlay } from "./BootstrapOverlay";

const fetchAgentSessionStatusMock = vi.fn();
const startAgentSessionMock = vi.fn();
vi.mock("@/lib/agents/session-client", () => ({
  fetchAgentSessionStatus: () => fetchAgentSessionStatusMock(),
  startAgentSession: () => startAgentSessionMock(),
}));

function inactiveStatus() {
  return {
    active: false,
    expiresAt: null,
    secondsRemaining: 0,
    targetChannelCount: 0,
  };
}

// Fixed future timestamp so the cache-flip useEffect in
// `useAgentSession` (which marks the session inactive when
// `expiresAt` passes) doesn't fire mid-test.
const FAR_FUTURE_EXPIRES_AT = "2099-01-01T00:00:00Z";

function activeStatus(secondsRemaining = 900) {
  return {
    active: true,
    expiresAt: FAR_FUTURE_EXPIRES_AT,
    secondsRemaining,
    targetChannelCount: 2,
  };
}

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  fetchAgentSessionStatusMock.mockReset();
  startAgentSessionMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("BootstrapOverlay", () => {
  it("returns null when a live video track is bound", async () => {
    fetchAgentSessionStatusMock.mockResolvedValue(inactiveStatus());
    const { container } = render(
      <Wrapper>
        <BootstrapOverlay hasActiveTrack={true} />
      </Wrapper>,
    );
    // The query resolves to inactive, but `hasActiveTrack` short-
    // circuits the visual derivation before that lands. The overlay
    // never paints.
    expect(container.querySelector("[data-testid='BootstrapOverlay.root']")).toBeNull();
  });

  it("renders the 'Sell me stuff' CTA in idle state", async () => {
    fetchAgentSessionStatusMock.mockResolvedValue(inactiveStatus());
    render(
      <Wrapper>
        <BootstrapOverlay hasActiveTrack={false} />
      </Wrapper>,
    );

    const root = await screen.findByTestId("BootstrapOverlay.root");
    expect(root.dataset.state).toBe("idle");
    expect(screen.getByTestId("BootstrapOverlay.sellMeStuffButton")).toHaveTextContent(
      /sell me stuff/i,
    );
  });

  it("swaps to the loading card while the start mutation is in flight", async () => {
    fetchAgentSessionStatusMock.mockResolvedValue(inactiveStatus());
    // Hang the mutation so we observe the `isStarting` slice — the
    // overlay must be in `starting` state for the duration the POST
    // hasn't returned.
    let resolveStart: (value: ReturnType<typeof activeStatus>) => void = () => {};
    startAgentSessionMock.mockImplementation(
      () =>
        new Promise((resolve) => {
          resolveStart = resolve;
        }),
    );

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(
      <Wrapper>
        <BootstrapOverlay hasActiveTrack={false} />
      </Wrapper>,
    );

    await user.click(await screen.findByTestId("BootstrapOverlay.sellMeStuffButton"));

    const card = await screen.findByTestId("BootstrapOverlay.loadingCard");
    expect(card).toHaveAttribute("aria-busy", "true");
    expect(card).toHaveAttribute("aria-live", "polite");

    // Clean up the dangling promise so the test runner doesn't
    // complain about unhandled rejections.
    await act(async () => {
      resolveStart(activeStatus());
    });
  });

  it("renders the loading card while a session is active but no track is bound (warming state)", async () => {
    fetchAgentSessionStatusMock.mockResolvedValue(activeStatus());
    render(
      <Wrapper>
        <BootstrapOverlay hasActiveTrack={false} />
      </Wrapper>,
    );

    const card = await screen.findByTestId("BootstrapOverlay.loadingCard");
    expect(card.textContent).toMatch(/warming up your stream/i);
    expect(screen.queryByTestId("BootstrapOverlay.sellMeStuffButton")).toBeNull();
  });

  // Regression: if LiveKit's `TrackSubscribed` never fires (network
  // drop, agent crash, SFU edge case) the warming card used to loop
  // its rotating copy forever. The watchdog forces the retry card
  // after a generous 45s budget so the viewer has an out.
  it("surfaces a retry path after the warming state exceeds the timeout", async () => {
    fetchAgentSessionStatusMock.mockResolvedValue(activeStatus());
    render(
      <Wrapper>
        <BootstrapOverlay hasActiveTrack={false} />
      </Wrapper>,
    );

    // Warming card shows first.
    const card = await screen.findByTestId("BootstrapOverlay.loadingCard");
    expect(card).toBeInTheDocument();

    // 45s pass without a track binding.
    await act(async () => {
      vi.advanceTimersByTime(45_000);
    });

    // Error / retry card replaces the warming loop.
    const root = await screen.findByTestId("BootstrapOverlay.root");
    expect(root.dataset.state).toBe("error");
    expect(screen.getByTestId("BootstrapOverlay.sellMeStuffButton")).toHaveTextContent(
      /try again/i,
    );
  });

  it("does not surface the retry card if the track binds before the timeout", async () => {
    fetchAgentSessionStatusMock.mockResolvedValue(activeStatus());
    const { rerender } = render(
      <Wrapper>
        <BootstrapOverlay hasActiveTrack={false} />
      </Wrapper>,
    );
    await screen.findByTestId("BootstrapOverlay.loadingCard");

    // Track binds at 30s.
    await act(async () => {
      vi.advanceTimersByTime(30_000);
    });
    rerender(
      <Wrapper>
        <BootstrapOverlay hasActiveTrack={true} />
      </Wrapper>,
    );

    // The overlay hides; advancing past the original timeout must
    // not resurrect the error card.
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(screen.queryByTestId("BootstrapOverlay.root")).toBeNull();
  });

  // When the agent-session timer hits 0 server-side, the LiveKit
  // track unbinds (`hasActiveTrack` flips to false) but the cached
  // `status.active` lags by up to one poll interval. The overlay must
  // not get stuck on the "warming" copy during that window — it should
  // return to the "Sell me stuff" CTA the moment `expiresAt` passes.
  it("returns to the 'Sell me stuff' CTA when the session times out", async () => {
    const expiresIn = 5_000;
    fetchAgentSessionStatusMock.mockResolvedValue({
      active: true,
      expiresAt: new Date(Date.now() + expiresIn).toISOString(),
      secondsRemaining: Math.floor(expiresIn / 1_000),
      targetChannelCount: 2,
    });

    render(
      <Wrapper>
        <BootstrapOverlay hasActiveTrack={false} />
      </Wrapper>,
    );

    // Warming state while the session is live but no track is bound.
    await screen.findByTestId("BootstrapOverlay.loadingCard");

    // Advance past expiresAt — the hook flips the cache to inactive.
    await act(async () => {
      vi.advanceTimersByTime(expiresIn + 100);
    });

    const root = await screen.findByTestId("BootstrapOverlay.root");
    expect(root.dataset.state).toBe("idle");
    expect(screen.getByTestId("BootstrapOverlay.sellMeStuffButton")).toHaveTextContent(
      /sell me stuff/i,
    );
  });

  it("falls back to a retry card when the start mutation errors", async () => {
    fetchAgentSessionStatusMock.mockResolvedValue(inactiveStatus());
    startAgentSessionMock.mockRejectedValue(new Error("agent_session_500"));

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(
      <Wrapper>
        <BootstrapOverlay hasActiveTrack={false} />
      </Wrapper>,
    );

    await user.click(await screen.findByTestId("BootstrapOverlay.sellMeStuffButton"));

    const button = await screen.findByTestId("BootstrapOverlay.sellMeStuffButton");
    expect(button).toHaveTextContent(/try again/i);
    expect(screen.getByRole("alert")).toHaveTextContent(/couldn.t start the stream/i);
  });
});
