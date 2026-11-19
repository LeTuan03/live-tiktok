import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import type { RemoteTrack } from "livekit-client";
import { LiveRoomPlayer } from "./LiveRoomPlayer";
import { HIDE_VIDEO_UI } from "@/lib/feed/flags";

function makeTrack(): RemoteTrack & {
  attach: ReturnType<typeof vi.fn>;
  detach: ReturnType<typeof vi.fn>;
} {
  return {
    attach: vi.fn(),
    detach: vi.fn(),
  } as unknown as RemoteTrack & {
    attach: ReturnType<typeof vi.fn>;
    detach: ReturnType<typeof vi.fn>;
  };
}

describe("LiveRoomPlayer", () => {
  it("renders the video and audio elements even with no track yet", () => {
    const { container } = render(
      <LiveRoomPlayer videoTrack={null} audioTrack={null} muted={true} />,
    );
    expect(container.querySelector("video")).not.toBeNull();
    expect(container.querySelector("audio")).not.toBeNull();
  });

  it("attaches video and audio tracks on mount", () => {
    const video = makeTrack();
    const audio = makeTrack();
    render(
      <LiveRoomPlayer videoTrack={video} audioTrack={audio} muted={true} onToggleMute={() => {}} />,
    );
    expect(video.attach).toHaveBeenCalledTimes(1);
    expect(audio.attach).toHaveBeenCalledTimes(1);
  });

  it("detaches tracks when unmounted", () => {
    const video = makeTrack();
    const audio = makeTrack();
    const { unmount } = render(
      <LiveRoomPlayer videoTrack={video} audioTrack={audio} muted={true} onToggleMute={() => {}} />,
    );
    unmount();
    expect(video.detach).toHaveBeenCalled();
    expect(audio.detach).toHaveBeenCalled();
  });

  it("re-attaches when the video track instance changes", () => {
    const v1 = makeTrack();
    const v2 = makeTrack();
    const { rerender } = render(<LiveRoomPlayer videoTrack={v1} audioTrack={null} muted={true} />);
    expect(v1.attach).toHaveBeenCalledTimes(1);
    rerender(<LiveRoomPlayer videoTrack={v2} audioTrack={null} muted={true} />);
    expect(v1.detach).toHaveBeenCalled();
    expect(v2.attach).toHaveBeenCalledTimes(1);
  });

  it("non-PiP: tapping the player toggles mute via onToggleMute", () => {
    const audio = makeTrack();
    const onToggleMute = vi.fn();
    const { container } = render(
      <LiveRoomPlayer
        videoTrack={null}
        audioTrack={audio}
        muted={true}
        onToggleMute={onToggleMute}
      />,
    );
    const audioEl = container.querySelector("audio") as HTMLAudioElement;
    audioEl.play = vi.fn(() => Promise.resolve()) as unknown as HTMLMediaElement["play"];
    fireEvent.click(screen.getByRole("button", { name: /unmute audio/i }));
    expect(onToggleMute).toHaveBeenCalledTimes(1);
  });

  it("non-PiP: does not render an in-player tap-to-unmute hint (mute UI lives on the side rail)", () => {
    const audio = makeTrack();
    render(
      <LiveRoomPlayer videoTrack={null} audioTrack={audio} muted={true} onToggleMute={() => {}} />,
    );
    expect(screen.queryByText(/tap to unmute/i)).toBeNull();
  });

  it("PiP: tap on the surface fires onTap (maximize), not mute", () => {
    const audio = makeTrack();
    const onTap = vi.fn();
    const onToggleMute = vi.fn();
    render(
      <LiveRoomPlayer
        videoTrack={null}
        audioTrack={audio}
        muted={true}
        onToggleMute={onToggleMute}
        onTap={onTap}
        isPip
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /maximize avatar/i }));
    expect(onTap).toHaveBeenCalledTimes(1);
    expect(onToggleMute).not.toHaveBeenCalled();
  });

  it.skipIf(HIDE_VIDEO_UI)("PiP: the corner mute icon toggles mute without firing onTap", () => {
    const audio = makeTrack();
    const onTap = vi.fn();
    const onToggleMute = vi.fn();
    render(
      <LiveRoomPlayer
        videoTrack={null}
        audioTrack={audio}
        muted={true}
        onToggleMute={onToggleMute}
        onTap={onTap}
        isPip
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /unmute audio/i }));
    expect(onToggleMute).toHaveBeenCalledTimes(1);
    expect(onTap).not.toHaveBeenCalled();
  });

  it("renders the static host placeholder image as the backdrop", () => {
    render(<LiveRoomPlayer videoTrack={null} audioTrack={null} muted={true} />);
    const placeholder = screen.getByTestId("host-background-placeholder");
    expect(placeholder).toBeInTheDocument();
    expect(placeholder.tagName).toBe("IMG");
    expect(placeholder).toHaveAttribute("src", "/personas/demo-host-9x16.png");
  });

  it("dims + blurs the placeholder while there's no live track (loading state)", () => {
    render(<LiveRoomPlayer videoTrack={null} audioTrack={null} muted={true} />);
    const placeholder = screen.getByTestId("host-background-placeholder");
    // The placeholder's data attribute is the test-facing contract for
    // the loading look (the precise class names are presentational).
    expect(placeholder.dataset.loading).toBe("true");
    expect(placeholder.className).toMatch(/\bblur-/);
    expect(placeholder.className).toMatch(/\bbrightness-/);
  });

  it("keeps the placeholder mounted once a video track is attached so the live frames composite over it", () => {
    const video = makeTrack();
    render(<LiveRoomPlayer videoTrack={video} audioTrack={null} muted={true} />);
    const placeholder = screen.getByTestId("host-background-placeholder");
    expect(placeholder).toBeInTheDocument();
    // With a track bound, the loading look comes off so live frames
    // composite over a clean still.
    expect(placeholder.dataset.loading).toBeUndefined();
    expect(placeholder.className).not.toMatch(/\bblur-/);
  });

  it.skipIf(HIDE_VIDEO_UI)(
    "renders a Connecting pill while there's no live track on the non-PiP surface",
    () => {
      render(<LiveRoomPlayer videoTrack={null} audioTrack={null} muted={true} />);
      expect(screen.getByTestId("live-room-connecting-pill")).toBeInTheDocument();
      expect(screen.getByText(/connecting/i)).toBeInTheDocument();
    },
  );

  it.skipIf(HIDE_VIDEO_UI)("hides the Connecting pill the moment a video track binds", () => {
    const video = makeTrack();
    const { rerender } = render(
      <LiveRoomPlayer videoTrack={null} audioTrack={null} muted={true} />,
    );
    expect(screen.queryByTestId("live-room-connecting-pill")).toBeInTheDocument();
    rerender(<LiveRoomPlayer videoTrack={video} audioTrack={null} muted={true} />);
    expect(screen.queryByTestId("live-room-connecting-pill")).toBeNull();
  });

  it.skipIf(HIDE_VIDEO_UI)(
    "does not render the Connecting pill on a PiP surface (no room for it on the small tile)",
    () => {
      render(
        <LiveRoomPlayer videoTrack={null} audioTrack={null} muted={true} isPip onTap={() => {}} />,
      );
      expect(screen.queryByTestId("live-room-connecting-pill")).toBeNull();
    },
  );
});

describe.skipIf(HIDE_VIDEO_UI)("LiveRoomPlayer ad-metadata overlay", () => {
  const ORIGINAL_BASE = process.env.NEXT_PUBLIC_API_BASE_URL;

  beforeEach(() => {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
    process.env.NEXT_PUBLIC_API_BASE_URL = ORIGINAL_BASE;
  });

  function adResponse(): Response {
    // Envelope shape matching the server's `CurrentAdResponse(ad=AdSnapshot)`
    // (see `server/src/soshop/api/routes/channels.py`). The fixture
    // previously used a flat hand-mirror that diverged from the
    // Pydantic model — the follow-up aligned the wire shape so
    // the `AdMetadataOverlay` (and the FeedPager mid-pitch seed path)
    // actually see populated product/persona/advertiser data.
    return new Response(
      JSON.stringify({
        ad: {
          id: "ad-1",
          product: {
            id: "prod-1",
            title: "Aurora Tea",
            price_cents: 1290,
            currency: "USD",
            image_url: "https://img.example/tea.png",
            deep_link_url: "https://shop.example/tea",
            landing_url: null,
          },
          persona: {
            id: "persona-1",
            display_name: "Sora",
            avatar_url: "https://img.example/sora.png",
          },
          advertiser: { id: "adv-1", name: "Aurora Goods" },
          deep_link_url: "https://shop.example/tea",
          started_at: "2024-01-01T00:00:00Z",
          duration_ms: 15_000,
        },
      }),
      {
        status: 200,
        headers: { "Content-Type": "application/json" },
      },
    );
  }

  it("polls /channels/{id}/current and renders product, persona, advertiser, Shop CTA", async () => {
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => adResponse());

    render(
      <LiveRoomPlayer videoTrack={null} audioTrack={null} muted={true} channelId="channel-0" />,
    );

    await waitFor(() => {
      expect(fetchSpy).toHaveBeenCalledTimes(1);
    });
    expect(fetchSpy.mock.calls[0][0]).toBe("https://api.example.test/channels/channel-0/current");

    await screen.findByTestId("ad-metadata-overlay");
    expect(screen.getByText("Aurora Tea")).toBeInTheDocument();
    expect(screen.getByText(/Aurora Goods/)).toBeInTheDocument();
    expect(screen.getByText(/Sora/)).toBeInTheDocument();
    const cta = screen.getByTestId("ad-shop-cta");
    expect(cta).toHaveAttribute("href", "https://shop.example/tea");
  });

  it("re-polls every 5 seconds while channelId is set", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => adResponse());

    render(
      <LiveRoomPlayer videoTrack={null} audioTrack={null} muted={true} channelId="channel-0" />,
    );

    // Flush the initial useEffect + fetch microtask.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(2);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(3);
  });

  it("stops polling when channelId becomes null and on unmount", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, "fetch").mockImplementation(async () => adResponse());

    const { rerender, unmount } = render(
      <LiveRoomPlayer videoTrack={null} audioTrack={null} muted={true} channelId="channel-0" />,
    );

    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    rerender(<LiveRoomPlayer videoTrack={null} audioTrack={null} muted={true} channelId={null} />);

    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(fetchSpy).toHaveBeenCalledTimes(1);
  });

  it("does not fetch when channelId is omitted", async () => {
    vi.useFakeTimers();
    const fetchSpy = vi.spyOn(globalThis, "fetch");
    render(<LiveRoomPlayer videoTrack={null} audioTrack={null} muted={true} />);
    await act(async () => {
      await vi.advanceTimersByTimeAsync(15_000);
    });
    expect(fetchSpy).not.toHaveBeenCalled();
  });
});
