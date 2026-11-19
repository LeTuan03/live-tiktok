import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { ReactNode } from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const pushMock = vi.fn();
vi.mock("next/navigation", () => ({
  useRouter: () => ({ push: pushMock, replace: vi.fn() }),
}));

const fetchAgentSessionStatusMock = vi.fn();
const startAgentSessionMock = vi.fn();
vi.mock("@/lib/agents/session-client", () => ({
  fetchAgentSessionStatus: () => fetchAgentSessionStatusMock(),
  startAgentSession: () => startAgentSessionMock(),
}));

import { ToastProvider } from "@/lib/ui/toast";
import { SoShopLogoButton } from "./SoShopLogoButton";

function Wrapper({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return (
    <QueryClientProvider client={client}>
      <ToastProvider>{children}</ToastProvider>
    </QueryClientProvider>
  );
}

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  pushMock.mockReset();
  fetchAgentSessionStatusMock.mockReset();
  startAgentSessionMock.mockReset();
});

afterEach(() => {
  vi.useRealTimers();
});

describe("SoShopLogoButton", () => {
  it("renders the SoShop wordmark with no chip when inactive", async () => {
    fetchAgentSessionStatusMock.mockResolvedValue({
      active: false,
      expiresAt: null,
      secondsRemaining: 0,
      targetChannelCount: 0,
    });

    render(
      <Wrapper>
        <SoShopLogoButton />
      </Wrapper>,
    );

    expect(await screen.findByText("SoShop")).toBeInTheDocument();
    expect(screen.queryByTestId("SoShopLogoButton.chip")).toBeNull();
  });

  it("starts a session on click and renders the chip from the mutation result", async () => {
    fetchAgentSessionStatusMock.mockResolvedValue({
      active: false,
      expiresAt: null,
      secondsRemaining: 0,
      targetChannelCount: 0,
    });
    startAgentSessionMock.mockResolvedValue({
      active: true,
      expiresAt: "2099-01-01T20:00:00Z",
      secondsRemaining: 1182,
      targetChannelCount: 2,
    });

    const user = userEvent.setup({ advanceTimers: vi.advanceTimersByTime });
    render(
      <Wrapper>
        <SoShopLogoButton />
      </Wrapper>,
    );

    await user.click(await screen.findByRole("button"));

    await waitFor(() => expect(startAgentSessionMock).toHaveBeenCalledTimes(1));
    const chip = await screen.findByTestId("SoShopLogoButton.chip");
    expect(chip.textContent).toContain("19:42");
    expect(pushMock).toHaveBeenCalledWith("/");
  });

  it("renders the chip when the query reports an active session", async () => {
    fetchAgentSessionStatusMock.mockResolvedValue({
      active: true,
      expiresAt: "2099-01-01T20:00:00Z",
      secondsRemaining: 65,
      targetChannelCount: 2,
    });

    render(
      <Wrapper>
        <SoShopLogoButton />
      </Wrapper>,
    );

    const chip = await screen.findByTestId("SoShopLogoButton.chip");
    expect(chip.textContent).toContain("01:05");

    await act(async () => {
      vi.advanceTimersByTime(2_000);
    });
    expect(chip.textContent).toContain("01:03");
  });
});
