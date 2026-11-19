import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from "vitest";
import {
  act,
  fireEvent,
  render as rtlRender,
  screen,
  waitFor,
  within,
  type RenderOptions,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement, ReactNode } from "react";
import type { RemoteAudioTrack, RemoteTrack, RemoteVideoTrack } from "livekit-client";
import { FeedPager } from "./FeedPager";

// FeedPager mounts a `BootstrapOverlay` on its active cell, which
// itself calls `useAgentSession` → `useQueryClient`. Tests must provide
// a `QueryClientProvider` or the overlay throws "No QueryClient set"
// at mount. Wrapping every render here with a fresh client keeps the
// existing test bodies untouched.
function TestProviders({ children }: { children: ReactNode }) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}

function render(ui: ReactElement, options?: Omit<RenderOptions, "wrapper">) {
  return rtlRender(ui, { wrapper: TestProviders, ...options });
}
import { HIDE_VIDEO_UI } from "@/lib/feed/flags";
import type {
  ActiveAudioTrackListener,
  ActivePitchListener,
  ActiveTrackListener,
  AudioPlaybackStatusListener,
  ChannelRoomCoordinator,
} from "@/lib/feed/ChannelRoomCoordinator";
import type { PitchEvent } from "@/lib/feed/PitchEventSource";

// jsdom doesn't ship IntersectionObserver — stub it inert so FeedPager
// can mount. We never trigger the observer callback from these tests;
// the swipe-driven paths are covered in their own suites.
beforeAll(() => {
  if (typeof globalThis.IntersectionObserver === "undefined") {
    class StubIO {
      observe() {}
      unobserve() {}
      disconnect() {}
      takeRecords() {
        return [];
      }
    }
    (globalThis as { IntersectionObserver: typeof IntersectionObserver }).IntersectionObserver =
      StubIO as unknown as typeof IntersectionObserver;
  }
  // `authedFetch` (used by `getCurrentAd`) throws when
  // `NEXT_PUBLIC_API_BASE_URL` is unset. The FeedPager now fires
  // `getCurrentAd` from its activeVirtual effect, so every test in
  // this file needs the env var bound to something (we mock the
  // fetch implementation anyway).
  if (!process.env.NEXT_PUBLIC_API_BASE_URL) {
    process.env.NEXT_PUBLIC_API_BASE_URL = "https://api.example.test";
  }
});

// Default fetch stub: every `getCurrentAd` call returns an empty
// `{ ad: null }` envelope so the FeedPager's mid-pitch seed effect
// doesn't blow up when a test doesn't care about it. Tests that DO
// exercise the seed path override this mock with a specific response.
beforeEach(() => {
  vi.spyOn(globalThis, "fetch").mockImplementation(
    async () =>
      new Response(JSON.stringify({ ad: null }), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      }),
  );
});

afterEach(() => {
  vi.restoreAllMocks();
});

function makeTrack(kind: "video" | "audio"): RemoteTrack & {
  attach: ReturnType<typeof vi.fn>;
  detach: ReturnType<typeof vi.fn>;
} {
  return {
    kind,
    attach: vi.fn(),
    detach: vi.fn(),
  } as unknown as RemoteTrack & {
    attach: ReturnType<typeof vi.fn>;
    detach: ReturnType<typeof vi.fn>;
  };
}

interface FakeCoordinator {
  coordinator: ChannelRoomCoordinator;
  videoTrack: RemoteVideoTrack;
  audioTrack: RemoteAudioTrack;
  startActiveAudio: ReturnType<typeof vi.fn>;
  /** Drive a pitch event into the most-recent `active-pitch-event`
   *  listener — the only way to seat a pinned product card in tests
   *  without going through a real LiveKit text-stream. */
  emitPitchEvent: (event: PitchEvent) => void;
}

function fakeCoordinator(overrides: { canPlaybackAudio?: boolean } = {}): FakeCoordinator {
  const videoTrack = makeTrack("video") as unknown as RemoteVideoTrack;
  const audioTrack = makeTrack("audio") as unknown as RemoteAudioTrack;
  const startActiveAudio = vi.fn(async () => undefined);
  let pitchListener: ActivePitchListener | null = null;
  const fake: Partial<ChannelRoomCoordinator> = {
    activeIndex: 0,
    channelCount: 3,
    getActiveTrack: () => videoTrack,
    getActiveAudioTrack: () => audioTrack,
    getCanPlaybackAudio: () => overrides.canPlaybackAudio ?? true,
    // Three channels with stable ids so the FeedPager's mid-pitch
    // seed effect can map the active ordinal to a `channels.id`. The
    // tests below mock `getCurrentAd` at the fetch level when they
    // care about the seed path; tests that don't care still need a
    // valid id to satisfy the effect's guard.
    getChannelId: (ord: number) => `channel-${ord}`,
    // No bootstrap ad by default — tests that care about the
    // synchronous bootstrap seed override the coordinator construction
    // (or override `getChannelCurrentAd` directly on the returned fake).
    getChannelCurrentAd: () => null,
    startActiveAudio,
    swipeNext: vi.fn(),
    swipePrev: vi.fn(),
    // No-op chat bridge for tests — the FeedPager always asks for one,
    // but the simulator (driven by `productId`) is the only message
    // source these specs exercise. Returns a `ChatBridge` so the
    // hook accepts it without an extra typeguard.
    getActiveChatBridge: () => ({
      subscribe: () => () => {},
      start: () => {},
      stop: () => {},
      publish: async () => undefined,
    }),
    publishActiveChat: async () => undefined,
    // The pager's `on()` calls fire once on registration with the current
    // value (matches the real coordinator's contract). Pitch events are
    // captured for tests to fire imperatively via `emitPitchEvent`.
    on: vi.fn(
      (
        event: string,
        listener:
          | ActiveTrackListener
          | ActiveAudioTrackListener
          | AudioPlaybackStatusListener
          | ActivePitchListener,
      ): (() => void) => {
        if (event === "active-track-changed") {
          (listener as ActiveTrackListener)(videoTrack);
        } else if (event === "active-audio-track-changed") {
          (listener as ActiveAudioTrackListener)(audioTrack);
        } else if (event === "audio-playback-status-changed") {
          (listener as AudioPlaybackStatusListener)(overrides.canPlaybackAudio ?? true);
        } else if (event === "active-pitch-event") {
          pitchListener = listener as ActivePitchListener;
        }
        return () => {};
      },
    ) as unknown as ChannelRoomCoordinator["on"],
  };
  return {
    coordinator: fake as ChannelRoomCoordinator,
    videoTrack,
    audioTrack,
    startActiveAudio,
    emitPitchEvent: (event) => {
      if (!pitchListener) throw new Error("no active-pitch-event listener registered");
      act(() => pitchListener!(event));
    },
  };
}

function showPitch(emit: FakeCoordinator["emitPitchEvent"]) {
  emit({
    kind: "show",
    productId: "prod-1",
    popup: {
      id: "prod-1",
      title: "Sample Product",
      image_url: "https://cdn.example/img.jpg",
      cta: "Tap to shop",
      deep_link_url: "soshop://product/prod-1",
      landing_url: "https://example.com/product",
    },
  } as PitchEvent);
}

describe.skipIf(HIDE_VIDEO_UI)("FeedPager — mute wiring", () => {
  it("renders the 'Tap to unmute' pill on first load (muted default)", () => {
    const { coordinator } = fakeCoordinator();
    render(<FeedPager coordinator={coordinator} channelCount={3} />);
    expect(screen.getByRole("button", { name: /tap to unmute/i })).toBeInTheDocument();
  });

  it("clicking the pill unmutes and calls startActiveAudio()", () => {
    const { coordinator, startActiveAudio } = fakeCoordinator();
    render(<FeedPager coordinator={coordinator} channelCount={3} />);

    fireEvent.click(screen.getByRole("button", { name: /tap to unmute/i }));

    expect(startActiveAudio).toHaveBeenCalledTimes(1);
    // Pill hides once unmuted.
    expect(screen.queryByRole("button", { name: /tap to unmute/i })).toBeNull();
  });

  it("keeps the pill visible while canPlaybackAudio is false even after a toggle", () => {
    const { coordinator, startActiveAudio } = fakeCoordinator({
      canPlaybackAudio: false,
    });
    render(<FeedPager coordinator={coordinator} channelCount={3} />);

    // Pill present on first paint because canPlaybackAudio=false.
    expect(screen.getByRole("button", { name: /tap to unmute/i })).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: /tap to unmute/i }));
    expect(startActiveAudio).toHaveBeenCalledTimes(1);

    // canPlaybackAudio is still false, so the pill stays surfaced for a
    // second tap (LK's autoplay gate hasn't reopened yet).
    expect(screen.getByRole("button", { name: /tap to unmute/i })).toBeInTheDocument();
  });

  it("only the active cell receives a non-null audio track", () => {
    const { coordinator, audioTrack } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);
    // FeedPager mounts one <LiveRoomPlayer> per cell; the audio track
    // attaches inside an effect. Only the center cell's audio element
    // should have seen the active audio track's attach() call.
    const attachMock = (audioTrack as unknown as { attach: ReturnType<typeof vi.fn> }).attach;
    expect(attachMock).toHaveBeenCalledTimes(1);
    // Sanity: three cells rendered.
    expect(container.querySelectorAll("[data-cell-ord]")).toHaveLength(3);
  });
});

describe("FeedPager — swipe commit", () => {
  // jsdom doesn't lay out elements; the FeedPager swipe handler reads
  // `offsetTop` / `offsetHeight` off each cell to decide if a scroll
  // landed on the prev or next slot. Stub those geometry getters so a
  // simulated scrollTop change is interpretable.
  function stubLayout(container: HTMLElement, cellHeight = 800) {
    const cells = container.querySelectorAll<HTMLElement>("[data-cell-ord]");
    cells.forEach((cell, idx) => {
      Object.defineProperty(cell, "offsetTop", {
        configurable: true,
        get: () => idx * cellHeight,
      });
      Object.defineProperty(cell, "offsetHeight", {
        configurable: true,
        get: () => cellHeight,
      });
    });
  }

  function getScroller(container: HTMLElement): HTMLElement {
    const el = container.querySelector<HTMLElement>("[role='feed']");
    if (!el) throw new Error("scroller not found");
    // Mark the scroller as supporting `onscrollend` so FeedPager wires
    // the modern listener path. Tests exercise the fast path the
    // production browsers we care about (Chrome / Safari 18.4+ / FF) take.
    Object.defineProperty(el, "onscrollend", {
      configurable: true,
      writable: true,
      value: null,
    });
    // jsdom doesn't implement `Element.scrollTo`; the recenter effect
    // calls it after every commit. Stub it so the call no-ops.
    if (typeof el.scrollTo !== "function") {
      (el as HTMLElement & { scrollTo: (...args: unknown[]) => void }).scrollTo = () => {};
    }
    return el;
  }

  function settle(scroller: HTMLElement, scrollTop: number) {
    scroller.scrollTop = scrollTop;
    act(() => {
      scroller.dispatchEvent(new Event("scrollend"));
    });
  }

  it("scroll down + scrollend commits a swipeNext()", () => {
    const { coordinator } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);
    stubLayout(container);
    // center cell at offsetTop=800; bottom cell at offsetTop=1600.
    settle(getScroller(container), 1600);
    expect(coordinator.swipeNext).toHaveBeenCalledTimes(1);
    expect(coordinator.swipePrev).not.toHaveBeenCalled();
  });

  it("scroll up + scrollend commits a swipePrev()", () => {
    const { coordinator } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);
    stubLayout(container);
    settle(getScroller(container), 0); // top cell at offsetTop=0.
    expect(coordinator.swipePrev).toHaveBeenCalledTimes(1);
    expect(coordinator.swipeNext).not.toHaveBeenCalled();
  });

  it("a settled scroll back at center is a no-op (released gesture)", () => {
    const { coordinator } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);
    stubLayout(container);
    settle(getScroller(container), 800); // back at center cell's offsetTop.
    expect(coordinator.swipeNext).not.toHaveBeenCalled();
    expect(coordinator.swipePrev).not.toHaveBeenCalled();
  });
});

describe.skipIf(HIDE_VIDEO_UI)("FeedPager — product-focus default", () => {
  it("auto-enters product-focus mode when a pitch lands (no external nav)", () => {
    const { coordinator, emitPitchEvent } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    showPitch(emitPitchEvent);

    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    // No viewer tap required — the cell flips into product-focus
    // mode the instant a pitch is pinned. The product takes the main
    // surface; the seller avatar is in a draggable PiP overlay.
    expect(activeCell.dataset.productInFocus).toBe("true");
    expect(activeCell.querySelector("a[href]")).toBeNull();
    expect(
      within(activeCell).getByRole("button", { name: /close product details/i }),
    ).toBeInTheDocument();
    // DraggablePiP and LiveRoomPlayer both expose "Maximize avatar"
    // — the DraggablePiP wraps the player, so two buttons answer the
    // same intent.
    expect(
      within(activeCell).getAllByRole("button", { name: /maximize avatar/i }).length,
    ).toBeGreaterThan(0);
  });

  it("tapping the close affordance swaps the seller into full-bleed and locks the product card at the bottom", () => {
    const { coordinator, emitPitchEvent } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    showPitch(emitPitchEvent);
    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    expect(activeCell.dataset.productInFocus).toBe("true");

    fireEvent.click(within(activeCell).getByRole("button", { name: /close product details/i }));

    expect(activeCell.dataset.productInFocus).toBeUndefined();
    expect(
      within(activeCell).getByRole("button", { name: /view sample product/i }),
    ).toBeInTheDocument();
    expect(within(activeCell).queryByRole("button", { name: /close product details/i })).toBeNull();
  });

  it("tapping the avatar-mode card re-enters product-focus mode", () => {
    const { coordinator, emitPitchEvent } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    showPitch(emitPitchEvent);
    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    fireEvent.click(within(activeCell).getByRole("button", { name: /close product details/i }));
    // A single tap on the (always-big) card body returns the surface
    // to product-focus mode — there is no collapsed/expanded toggle
    // step anymore.
    fireEvent.click(within(activeCell).getByRole("button", { name: /view sample product/i }));

    expect(activeCell.dataset.productInFocus).toBe("true");
    expect(
      within(activeCell).getByRole("button", { name: /close product details/i }),
    ).toBeInTheDocument();
  });

  // Regression: the agent's `signal_pitch_end` (or a fresh rotation
  // tick) fires a `dismiss` envelope at the END of the pitch. While
  // the viewer is browsing the focus surface, that dismiss must NOT
  // close their focus view. The FeedPager snapshots the product into
  // `focusedProduct` so dismiss/show on the live `pinnedProduct`
  // doesn't yank the surface out from under the viewer.
  it("agent dismiss does NOT close the (default) focus view", () => {
    const { coordinator, emitPitchEvent } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    showPitch(emitPitchEvent);
    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    expect(activeCell.dataset.productInFocus).toBe("true");

    // Agent's signal_pitch_end fires while user is browsing photos.
    emitPitchEvent({ kind: "dismiss", productId: "prod-1" });

    // Focus stays put — close button is still on screen, seller PiP
    // is still mounted.
    expect(activeCell.dataset.productInFocus).toBe("true");
    expect(
      within(activeCell).getByRole("button", { name: /close product details/i }),
    ).toBeInTheDocument();
  });

  // The avatar-mode card must persist on screen between ads on the
  // same channel — the agent fires `dismiss` (signal_pitch_end) at the
  // end of every pitch, and there's a brief gap before the next
  // pitch's `show` arrives. The handoff is inline: card stays locked
  // in place, content swaps when the new show lands.
  it("a dismiss event keeps the avatar-mode card so it persists until the next ad", () => {
    const { coordinator, emitPitchEvent } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    showPitch(emitPitchEvent);
    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    // Hop over to avatar mode so the card is the on-screen surface.
    fireEvent.click(within(activeCell).getByRole("button", { name: /close product details/i }));
    expect(
      within(activeCell).getByRole("button", { name: /view sample product/i }),
    ).toBeInTheDocument();

    // Agent's signal_pitch_end at the end of the ad — card must stay.
    emitPitchEvent({ kind: "dismiss", productId: "prod-1" });

    expect(
      within(activeCell).getByRole("button", { name: /view sample product/i }),
    ).toBeInTheDocument();
  });

  it("a fresh show event after a dismiss swaps the avatar-mode card inline", () => {
    const { coordinator, emitPitchEvent } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    showPitch(emitPitchEvent);
    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    // Swap to avatar mode so the card is the surface under test.
    fireEvent.click(within(activeCell).getByRole("button", { name: /close product details/i }));
    emitPitchEvent({ kind: "dismiss", productId: "prod-1" });

    // Next ad lands; the card swaps from product 1 → product 2 in place.
    emitPitchEvent({
      kind: "show",
      productId: "prod-2",
      popup: {
        id: "prod-2",
        title: "Next Product",
        image_url: "https://cdn.example/img2.jpg",
        cta: "Tap to shop",
        deep_link_url: "soshop://product/prod-2",
        landing_url: "https://example.com/next-product",
      },
    } as PitchEvent);

    expect(
      within(activeCell).getByRole("button", { name: /view next product/i }),
    ).toBeInTheDocument();
    expect(within(activeCell).queryByRole("button", { name: /view sample product/i })).toBeNull();
  });

  // When the agent rotates to a new ad on this channel mid-focus
  // (next persona's product), the gallery must follow — otherwise
  // the viewer hears product B being pitched while looking at
  // product A's photos. The shared `product_pitch` text-stream
  // delivers the same `show` event to every viewer in the room, so
  // all web clients swap in lockstep.
  // Regression: the chat overlay used to live inside both branches of
  // the avatar/product ternary, so every mode toggle unmounted one
  // instance and mounted another — disposing the rolling message
  // buffer mid-stream. The fix lifts the overlay above the ternary so
  // the same DOM node (and therefore the same `GlobalChatStore`)
  // survives the toggle. Asserting node identity is the tightest
  // version of "the chat doesn't restart on mode switch."
  it("keeps the chat overlay as a single stable DOM node across the mode toggle", () => {
    const { coordinator, emitPitchEvent } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    showPitch(emitPitchEvent);
    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    expect(activeCell.dataset.productInFocus).toBe("true");

    const overlayInProductMode = activeCell.querySelector('[data-testid="GlobalChatOverlay"]');
    expect(overlayInProductMode).not.toBeNull();

    // Flip to avatar mode.
    fireEvent.click(within(activeCell).getByRole("button", { name: /close product details/i }));
    expect(activeCell.dataset.productInFocus).toBeUndefined();

    const overlayInAvatarMode = activeCell.querySelector('[data-testid="GlobalChatOverlay"]');
    // Same node — React reconciled around the ternary swap.
    expect(overlayInAvatarMode).toBe(overlayInProductMode);

    // And only ever one overlay on the active cell, regardless of mode.
    expect(activeCell.querySelectorAll('[data-testid="GlobalChatOverlay"]').length).toBe(1);

    // Back to product mode — still the same node.
    fireEvent.click(within(activeCell).getByRole("button", { name: /view sample product/i }));
    expect(activeCell.dataset.productInFocus).toBe("true");
    const overlayBackInProductMode = activeCell.querySelector('[data-testid="GlobalChatOverlay"]');
    expect(overlayBackInProductMode).toBe(overlayInProductMode);
  });

  it("a fresh show event while in focus mode swaps the focused product to match the live pitch", () => {
    const { coordinator, emitPitchEvent } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    showPitch(emitPitchEvent);
    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    expect(activeCell.dataset.productInFocus).toBe("true");

    // Next persona's product lands on the wire — focus surface
    // follows the live pin.
    emitPitchEvent({
      kind: "show",
      productId: "prod-2",
      popup: {
        id: "prod-2",
        title: "Another Product",
        image_url: "https://cdn.example/img2.jpg",
        cta: "Tap to shop",
        deep_link_url: "soshop://product/prod-2",
        landing_url: "https://example.com/product-2",
      },
    } as PitchEvent);

    expect(activeCell.dataset.productInFocus).toBe("true");
    expect(
      within(activeCell).getByRole("heading", { name: /another product/i }),
    ).toBeInTheDocument();
    expect(within(activeCell).queryByRole("heading", { name: /sample product/i })).toBeNull();
  });
});

// `getCurrentAd` seed path: a viewer who joins a channel mid-pitch
// sees the airing product surface immediately — the cell defaults
// into product-focus mode using the REST snapshot. LiveKit text-
// streams don't replay for late joiners, so the REST snapshot fills
// the gap.
describe.skipIf(HIDE_VIDEO_UI)("FeedPager — mid-pitch join seed", () => {
  function seedResponse(productId: string, title: string): Response {
    return new Response(
      JSON.stringify({
        ad: {
          id: "ad-seed",
          product: {
            id: productId,
            title,
            price_cents: 4900,
            currency: "USD",
            image_url: "https://cdn.example/seed.jpg",
            deep_link_url: "https://shop.example/seed",
            landing_url: null,
          },
          persona: {
            id: "persona-seed",
            display_name: "Leila",
            avatar_url: null,
          },
          advertiser: { id: "adv-seed", name: "Aurora Goods" },
          deep_link_url: "https://shop.example/seed",
          started_at: "2024-01-01T00:00:00Z",
          duration_ms: 15_000,
        },
      }),
      { status: 200, headers: { "Content-Type": "application/json" } },
    );
  }

  it("seeds product-focus mode from /channels/{id}/current on first mount", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      seedResponse("prod-seed", "Seeded Earbuds"),
    );

    const { coordinator } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    // The seeded product takes over the cell as the default focus
    // surface; no viewer tap required.
    await waitFor(() => {
      expect(activeCell.dataset.productInFocus).toBe("true");
      expect(
        within(activeCell).getByRole("heading", { name: /seeded earbuds/i }),
      ).toBeInTheDocument();
    });
  });

  it("toggling to avatar mode after a seeded mount surfaces the locked product card", async () => {
    vi.spyOn(globalThis, "fetch").mockImplementation(async () =>
      seedResponse("prod-seed", "Seeded Earbuds"),
    );

    const { coordinator } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    await waitFor(() => {
      expect(activeCell.dataset.productInFocus).toBe("true");
    });
    // Viewer taps close → avatar full-bleed. The locked product card
    // sits at the bottom holding the seeded product.
    fireEvent.click(within(activeCell).getByRole("button", { name: /close product details/i }));
    expect(
      within(activeCell).getByRole("button", { name: /view seeded earbuds/i }),
    ).toBeInTheDocument();
  });

  // Regression for the "card disappears after I scroll" report: when
  // the coordinator carries a bootstrap `currentAd` for the active
  // channel, the FeedPager must surface that card synchronously on
  // swipe — even when the per-swipe REST refresh returns `{ ad: null }`
  // (channel hasn't logged an ad since page load, or transient REST
  // failure). Previously the seed effect wiped the card and waited on
  // the REST round-trip; on a channel with no `channel_ad_log` row
  // yet, that wait never finished and the user sat with a blank card.
  it("falls back to the bootstrap currentAd when the REST refresh returns null", async () => {
    // REST refresh returns the default `{ ad: null }` (set in the
    // file-level `beforeEach`). The bootstrap snapshot is the *only*
    // thing that can pin the card here.
    const { coordinator } = fakeCoordinator();
    (
      coordinator as unknown as {
        getChannelCurrentAd: (ord: number) => unknown;
      }
    ).getChannelCurrentAd = (ord: number) =>
      ord === 0
        ? {
            id: "ad-bootstrap",
            product: {
              id: "prod-bootstrap",
              title: "Bootstrap Pillow",
              priceCents: 5800,
              currency: "USD",
              imageUrl: "https://cdn.example/pillow.jpg",
              deepLinkUrl: "demoshop://products/wl-001",
              landingUrl: null,
            },
            deepLinkUrl: "demoshop://products/wl-001",
            startedAt: "2024-01-01T00:00:00Z",
            durationMs: 15_000,
          }
        : null;

    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    // Focus surface materialises synchronously from the bootstrap
    // snapshot — no `waitFor` needed because there's no network
    // round-trip in the path that pins it.
    expect(activeCell.dataset.productInFocus).toBe("true");
    expect(
      within(activeCell).getByRole("heading", { name: /bootstrap pillow/i }),
    ).toBeInTheDocument();
  });

  it("a fresh `show` event wins the race against the REST seed", async () => {
    // The REST fetch never resolves — simulating a slow API. The
    // `show` event arrives first and pins the product; the seed
    // effect must not overwrite it when the fetch eventually settles.
    let resolveFetch: (r: Response) => void = () => {};
    vi.spyOn(globalThis, "fetch").mockImplementation(
      () =>
        new Promise<Response>((resolve) => {
          resolveFetch = resolve;
        }),
    );

    const { coordinator, emitPitchEvent } = fakeCoordinator();
    const { container } = render(<FeedPager coordinator={coordinator} channelCount={3} />);

    showPitch(emitPitchEvent);
    const activeCell = container.querySelector('[data-cell-ord="1"]') as HTMLElement;
    expect(activeCell.dataset.productInFocus).toBe("true");
    expect(
      within(activeCell).getByRole("heading", { name: /sample product/i }),
    ).toBeInTheDocument();

    // Resolve the seed fetch *after* the text-stream show has already
    // pinned a product. The seed must not clobber the live pin.
    await act(async () => {
      resolveFetch(seedResponse("prod-seed", "Seeded Earbuds"));
    });

    // The original (text-stream) product is still in the focus
    // surface; the seeded one isn't.
    expect(
      within(activeCell).getByRole("heading", { name: /sample product/i }),
    ).toBeInTheDocument();
    expect(within(activeCell).queryByRole("heading", { name: /seeded earbuds/i })).toBeNull();
  });
});
