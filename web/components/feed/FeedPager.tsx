"use client";
// Virtualized infinite scroll-snap pager over the channel ring.
//
// The "infinite" feeling comes from a virtual `activeVirtual` index that
// grows monotonically; `activeVirtual mod channelCount` folds it back into
// the ring, so the user can scroll forever without ever hitting an end.
//
// Only three cells are ever in the DOM at once — `above`, `active`,
// `below`. Each cell mounts a single `<LiveRoomPlayer>` whose `videoTrack`
// prop is re-bound on commit; the center cell binds the live track from
// the coordinator and the neighbors keep `null` (which renders an empty
// black surface). Mounting a player per virtual index would defeat the
// point: the channel coordinator subscribes video on the active room only,
// so neighbor players have nothing to render anyway.
//
// Commit flow (CSS scroll-snap + `scrollend`):
//   1. User scroll lifts the bottom (or top) cell past the snap point.
//   2. `scroll-snap-stop: always` (via Tailwind's `snap-always`) keeps
//      the swipe to a single page even on a fast flick.
//   3. After the browser finishes snapping, `scrollend` fires once with
//      the final scrollTop sitting on the new cell's offsetTop. We diff
//      against the center cell's offsetTop to pick a direction.
//   4. Call `coordinator.swipeNext()` / `swipePrev()` and bump
//      `activeVirtual` by ±1 (also drives the modulo).
//   5. `useLayoutEffect` recenters the scroller silently before paint, so
//      the user's eyes track a single continuous channel transition
//      rather than seeing the cell stack reset to the middle.
//
// Why not `IntersectionObserver`: it fires the moment a neighbor crosses
// 50% visibility, which is *during* the swipe gesture. Recycling the cell
// stack mid-gesture fights the user's touch — the cells reshuffle while
// they're still scrolling, which reads as "flipping through multiple
// pages of content" (the same TikTok/Reels feed feels glassy because
// they wait for the snap to settle). `scrollend` (Chrome 114+, Firefox
// 109+, Safari 18.4+) gives us that "settled" signal directly; older
// browsers fall back to a debounced `scroll` listener.

import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { GlobalChatBridge } from "@/lib/chat/useGlobalChat";
import type { RemoteAudioTrack, RemoteVideoTrack } from "livekit-client";
import type { ChannelCurrentAd, ChannelRoomCoordinator } from "@/lib/feed/ChannelRoomCoordinator";
import { BlurredBackdrop } from "@/components/feed/BlurredBackdrop";
import { BootstrapOverlay } from "@/components/feed/BootstrapOverlay";
import { DraggablePiP } from "@/components/feed/DraggablePiP";
import { GlobalChatOverlay } from "@/components/feed/GlobalChatOverlay";
import { ProductFocusView } from "@/components/feed/ProductFocusView";
import { LiveRoomPlayer } from "@/components/live/LiveRoomPlayer";
import { PinnedProductCard } from "@/components/live/PinnedProductCard";
import type { PinnedProduct, PinnedProductMedia } from "@/components/live/types";
import type { PitchPopupMediaItem } from "@/lib/feed/PitchEventSource";
import { getCurrentAd } from "@/lib/api/channels";
import { HIDE_VIDEO_UI } from "@/lib/feed/flags";
import { Volume2 } from "lucide-react";

// Seller-avatar PiP. 9:16 ratio matches the agent's portrait publish
// format so the live video lands edge-to-edge in the PiP frame with
// no letterboxing.
//
// On mobile portrait the column fills the viewport, so the PiP is
// sized as a percentage of viewport width to match Apple's PiP HIG
// (~40 % of screen width — see AVPictureInPictureController defaults).
// On desktop the column is letterboxed to a phone-shaped frame; the
// desktop PiP is sized at ~32% of that column (~25% of the typical
// laptop column width). Matches the band TikTok Live / Whatnot use for
// portrait corner avatars on desktop and Apple AVKit's "small" preset
// (~¼ of the screen's longest edge).
const MOBILE_BREAKPOINT_PX = 768;
const MOBILE_PIP_WIDTH_RATIO = 0.42;
const MOBILE_PIP_MAX_W = 220;
const DESKTOP_PIP_W = 192;
const DESKTOP_PIP_H = 340;

function computeSellerPipSize(viewportWidth: number): { w: number; h: number } {
  if (viewportWidth < MOBILE_BREAKPOINT_PX) {
    const w = Math.min(Math.round(viewportWidth * MOBILE_PIP_WIDTH_RATIO), MOBILE_PIP_MAX_W);
    return { w, h: Math.round((w * 16) / 9) };
  }
  return { w: DESKTOP_PIP_W, h: DESKTOP_PIP_H };
}

export interface FeedPagerProps {
  coordinator: ChannelRoomCoordinator;
  /** Number of channels in the ring. The cell-to-channel modulo uses
   *  this; pass `coordinator.channelCount` after `init()` resolves. */
  channelCount: number;
}

/** Stable position within the 3-cell window. */
type CellOrdinal = 0 | 1 | 2;
const CELL_ORDINALS: readonly CellOrdinal[] = [0, 1, 2];
const CENTER_ORD: CellOrdinal = 1;

interface CellSlot {
  ord: CellOrdinal;
  /** Channel index (0..N-1) currently displayed in this cell. */
  channelOrdinal: number;
  /** `loopIndex * N + channelOrdinal` — strictly monotonic across the
   *  whole pager session. Used as the React key so the cells correctly
   *  reconcile when the user wraps past the channel boundary. */
  virtualKey: number;
}

export function FeedPager({ coordinator, channelCount }: FeedPagerProps) {
  // Monotonic virtual position; modulo gives the live channel ordinal.
  // Seeded from the coordinator so a hot-reload mid-session doesn't snap
  // the user back to channel 0.
  const [activeVirtual, setActiveVirtual] = useState(() => coordinator.activeIndex);
  const [activeTrack, setActiveTrack] = useState<RemoteVideoTrack | null>(() =>
    coordinator.getActiveTrack(),
  );
  const [activeAudioTrack, setActiveAudioTrack] = useState<RemoteAudioTrack | null>(() =>
    coordinator.getActiveAudioTrack(),
  );
  // Sticky mute state — once the user unmutes, audio stays on across
  // swipes. Default muted because every browser autoplay policy requires
  // a user gesture before audio can play.
  const [audioMuted, setAudioMuted] = useState(true);
  const [canPlaybackAudio, setCanPlaybackAudio] = useState(true);

  // Responsive seller-PiP sizing — seeded with the desktop fallback so
  // SSR + the first client render agree, then re-measured on mount and
  // on viewport resize. The PiP itself starts invisible (DraggablePiP
  // gates opacity on a measured anchor) so the size flip is not visible
  // to the user on mobile.
  const [sellerPipSize, setSellerPipSize] = useState(() => ({
    w: DESKTOP_PIP_W,
    h: DESKTOP_PIP_H,
  }));
  useEffect(() => {
    const measure = () => setSellerPipSize(computeSellerPipSize(window.innerWidth));
    measure();
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, []);

  // Bridge the coordinator's imperative active-track snapshot into React
  // state so the center cell rebinds when a remote track arrives or
  // unsubscribes after a swipe.
  useEffect(() => {
    return coordinator.on("active-track-changed", (track) => {
      setActiveTrack(track);
    });
  }, [coordinator]);

  useEffect(() => {
    return coordinator.on("active-audio-track-changed", (track) => {
      setActiveAudioTrack(track);
    });
  }, [coordinator]);

  useEffect(() => {
    return coordinator.on("audio-playback-status-changed", (flag) => {
      setCanPlaybackAudio(flag);
    });
  }, [coordinator]);

  // Toggle handler runs inside the click task, so the `startActiveAudio()`
  // call lands under the browser's transient activation window. We flip
  // the mute flag eagerly — LK's `startAudio()` rejection is non-fatal
  // and the next tap retries.
  const handleToggleAudio = useCallback(() => {
    setAudioMuted((prev) => {
      const next = !prev;
      if (!next) {
        void coordinator.startActiveAudio();
      }
      return next;
    });
  }, [coordinator]);

  // Pinned product card for the active cell, driven by the agent's
  // `product_pitch` text-stream. A `show` payload pins (or swaps) the
  // product; the matching `dismiss` (signal_pitch_end) is intentionally
  // a no-op so the card persists between ads on the same channel — the
  // next ad's `show` event swaps the content inline. The card only
  // clears on a channel swipe (see the `activeVirtual` seed below) or
  // when a `show` arrives with no popup (defensive — the agent never
  // emits that today).
  const [pinnedProduct, setPinnedProduct] = useState<PinnedProduct | null>(null);
  useEffect(() => {
    return coordinator.on("active-pitch-event", (event) => {
      if (event.kind === "dismiss") return;
      const popup = event.popup;
      if (!popup) return;
      setPinnedProduct({
        productId: event.productId,
        title: popup.title ?? "",
        imageUrl: popup.image_url,
        media: normalizeMedia(popup.media),
        cta: popup.cta,
        deepLinkUrl: popup.deep_link_url,
        landingUrl: popup.landing_url,
        pinnedAt: Date.now(),
      });
    });
  }, [coordinator]);

  // Seed the pinned card on every channel change. Two sources fill the
  // card on swipe, in this order:
  //
  //   1. **Bootstrap snapshot** — synchronous. Each channel returned by
  //      `GET /channels` carries its currently-airing `currentAd` at
  //      page load. The coordinator stashes those, and we pull the
  //      target channel's snapshot the instant `activeVirtual` updates
  //      — no network round-trip, no flicker. This is what guarantees
  //      the user always sees a card on a channel that's airing an ad,
  //      even when the REST refresh below fails or returns null.
  //
  //   2. **REST refresh** — asynchronous. `GET /channels/{id}/current`
  //      may return fresher data than the bootstrap (the orchestrator
  //      may have rotated to a new persona/product since page load).
  //      We replace the pinned card only when the refresh names a
  //      *different* product — same product = same card, no need to
  //      reset the user's compact-pill / expanded state.
  //
  // We do NOT pre-clear `pinnedProduct` here. Previously the effect
  // wiped the card synchronously and waited on the REST round-trip to
  // restore it; on channels with no `channel_ad_log` row yet (or any
  // transient fetch failure) the card never came back. Seeding from
  // the bootstrap first eliminates that gap entirely.
  //
  // Guards:
  //   * Abort the in-flight refresh if the user swipes again before it
  //     resolves (the result would land on the wrong channel).
  //   * Preserve a fresh `show` event that won the race against the
  //     REST refresh (`setPinnedProduct((cur) => cur && ...)`).
  useEffect(() => {
    if (channelCount <= 0) {
      setPinnedProduct(null);
      return;
    }
    const channelOrdinal = ((activeVirtual % channelCount) + channelCount) % channelCount;

    const bootstrap = coordinator.getChannelCurrentAd(channelOrdinal);
    if (bootstrap) {
      setPinnedProduct(pinnedFromCurrentAd(bootstrap));
    } else {
      setPinnedProduct(null);
    }

    const channelId = coordinator.getChannelId(channelOrdinal);
    if (!channelId) return;
    const controller = new AbortController();
    let aborted = false;
    (async () => {
      try {
        const current = await getCurrentAd(channelId, controller.signal);
        if (aborted) return;
        const product = current?.product;
        if (!product) return;
        setPinnedProduct((existing) => {
          // Same product already pinned (from bootstrap or a fresh
          // `show` event) — keep the existing card so a compact-pill
          // collapse / focus-view tap survives the refresh.
          if (existing && existing.productId === product.id) return existing;
          // A fresh `show` event landed while we were fetching. We
          // recognise that case by either: (a) no bootstrap to compare
          // against, in which case any existing pin came from the
          // wire; or (b) the existing pin doesn't match bootstrap, so
          // it must have been overwritten by a `show` event. The wire-
          // side pin is more authoritative than this snapshot REST.
          if (existing && !bootstrap) return existing;
          if (existing && bootstrap && existing.productId !== bootstrap.product.id) {
            return existing;
          }
          const media: PinnedProductMedia[] = (product.media ?? []).map((m) => ({
            position: m.position,
            kind: m.kind === "video" ? "video" : "image",
            url: m.url,
          }));
          return {
            productId: product.id,
            title: product.title,
            imageUrl: product.imageUrl ?? undefined,
            media: media.length > 0 ? media : undefined,
            priceCents: product.priceCents ?? undefined,
            currency: product.currency,
            deepLinkUrl: product.deepLinkUrl ?? undefined,
            landingUrl: product.landingUrl ?? undefined,
            pinnedAt: Date.now(),
          };
        });
      } catch (err) {
        if (aborted) return;
        const name = (err as { name?: string } | null)?.name;
        if (name === "AbortError") return;
        // Transient REST failure: the bootstrap-seeded card stays on
        // screen, so the user sees something for the new channel even
        // when the refresh is down.
      }
    })();
    return () => {
      aborted = true;
      controller.abort();
    };
  }, [activeVirtual, channelCount, coordinator]);

  // Product-focus is the *default* surface on the active cell:
  // whenever a product is pinned, it fills the cell and the seller
  // avatar lives in a corner PiP. The viewer taps the PiP (or the
  // close X) to swap roles — avatar full-bleed with the product card
  // locked at the bottom — and taps the card to swap back.
  //
  // The focus surface tracks the live `pinnedProduct` directly: when
  // the agent rotates to a new ad on this channel (next `show` event
  // on the `product_pitch` text-stream), the gallery swaps in
  // lockstep so the on-screen product matches the pitch the viewer
  // is hearing. All web clients on the same channel receive the same
  // text-stream event and update simultaneously — there's no per-
  // client snapshot to drift apart. `dismiss` events are still
  // ignored in the `active-pitch-event` handler above, so the focus
  // view survives the brief gap between an ad's end and the next
  // ad's `show`.
  //
  // `avatarFullBleed` is per-channel viewer intent — sticky for the
  // current channel, reset on swipe so the next channel re-enters
  // the product-focus default.
  const [avatarFullBleed, setAvatarFullBleed] = useState(false);
  useEffect(() => {
    setAvatarFullBleed(false);
  }, [activeVirtual]);

  const focusedProduct = avatarFullBleed ? null : pinnedProduct;

  const enterAvatarMode = useCallback(() => {
    setAvatarFullBleed(true);
  }, []);

  const enterProductMode = useCallback(() => {
    setAvatarFullBleed(false);
  }, []);

  // Stable bridge handle into the coordinator's active-channel chat
  // text-stream. `getActiveChatBridge()` returns a thin façade — the
  // underlying source flips automatically on swipe, so this single
  // reference stays correct across the whole pager lifetime.
  const chatBridge = useMemo<GlobalChatBridge | null>(
    () => coordinator.getActiveChatBridge(),
    [coordinator],
  );

  const scrollerRef = useRef<HTMLDivElement | null>(null);
  // Indexed by `CellOrdinal` so `cellRefs.current[CENTER_ORD]` is the
  // middle cell — used by the recenter effect and the IO setup.
  const cellRefs = useRef<Array<HTMLElement | null>>([null, null, null]);

  // Suppress IO callbacks fired between the commit and the layout-effect
  // recenter — without this the observer can see the pre-recenter scroll
  // position and fire a second swipe for the same gesture.
  const dispatchInFlightRef = useRef(false);

  // With a single channel there's nowhere to swipe — render only the
  // center cell so a stray drag can't reveal an identical neighbor cell
  // and the scroll-snap container has no off-screen content to flash.
  const isSingleChannel = channelCount === 1;

  // Derive 3-cell window. `((v % N) + N) % N` is the JS-correct positive
  // modulo so `activeVirtual - 1` wraps cleanly when the user is on
  // channel 0.
  const cells = useMemo<CellSlot[]>(() => {
    if (channelCount <= 0) return [];
    if (isSingleChannel) {
      return [{ ord: CENTER_ORD, virtualKey: activeVirtual, channelOrdinal: 0 }];
    }
    const wrap = (n: number) => ((n % channelCount) + channelCount) % channelCount;
    return CELL_ORDINALS.map((ord) => {
      const offset = ord - CENTER_ORD;
      const virt = activeVirtual + offset;
      return { ord, virtualKey: virt, channelOrdinal: wrap(virt) };
    });
  }, [activeVirtual, channelCount, isSingleChannel]);

  // Recenter the scroller back onto the middle cell after every commit.
  // `useLayoutEffect` runs synchronously after DOM mutation and before
  // paint, so the wrap is invisible — the cell that was the bottom of
  // the previous render is now the middle, in the same physical
  // viewport position. Skipped when only one channel exists: there is
  // no 3-cell window to recenter and the scroller has no overflow.
  useLayoutEffect(() => {
    if (isSingleChannel) return;
    const scroller = scrollerRef.current;
    const middle = cellRefs.current[CENTER_ORD];
    if (!scroller || !middle) return;
    if (scroller.scrollTop !== middle.offsetTop) {
      scroller.scrollTo({ top: middle.offsetTop });
    }
    dispatchInFlightRef.current = false;
  }, [activeVirtual, isSingleChannel]);

  // Commit a swipe once the scroll settles. Picks the direction by
  // comparing the final scrollTop to the center cell's offsetTop — a
  // settled scroll near the top cell means "swipe prev"; near the
  // bottom cell means "swipe next"; anywhere near center is a
  // released/cancelled gesture and stays a no-op.
  useEffect(() => {
    if (isSingleChannel) return;
    const scroller = scrollerRef.current;
    if (!scroller) return;

    const onSettled = () => {
      if (dispatchInFlightRef.current) return;
      const middle = cellRefs.current[CENTER_ORD];
      if (!middle) return;
      const cellHeight = middle.offsetHeight;
      if (cellHeight <= 0) return;
      const delta = scroller.scrollTop - middle.offsetTop;
      // Half a cell is the same commit threshold the previous IO
      // observer used; with `scroll-snap-stop: always` the snap will
      // have landed cleanly on either ±cellHeight or 0, so anything
      // beyond ±cellHeight/2 is unambiguously a committed swipe.
      if (Math.abs(delta) < cellHeight / 2) return;
      dispatchInFlightRef.current = true;
      if (delta > 0) {
        coordinator.swipeNext();
        setActiveVirtual((v) => v + 1);
      } else {
        coordinator.swipePrev();
        setActiveVirtual((v) => v - 1);
      }
    };

    // `scrollend` is the canonical signal but only shipped in Chrome
    // 114+ / Firefox 109+ / Safari 18.4+. On older browsers we
    // debounce on `scroll` instead — the timer waits long enough that
    // a snap animation will have finished before we sample scrollTop.
    // Feature-detect on the scroller via a Record cast so the runtime
    // check isn't narrowed away by TS' `in`-operator typing.
    const hasOnScrollend =
      (scroller as unknown as Record<string, unknown>).onscrollend !== undefined ||
      "onscrollend" in (scroller as unknown as Record<string, unknown>);
    if (hasOnScrollend) {
      scroller.addEventListener("scrollend", onSettled);
      return () => {
        scroller.removeEventListener("scrollend", onSettled);
      };
    }

    let timer: ReturnType<typeof setTimeout> | null = null;
    const onScroll = () => {
      if (timer !== null) clearTimeout(timer);
      timer = setTimeout(onSettled, 120);
    };
    scroller.addEventListener("scroll", onScroll, { passive: true });
    return () => {
      if (timer !== null) clearTimeout(timer);
      scroller.removeEventListener("scroll", onScroll);
    };
    // Re-attach when channelCount transitions through 0 — the
    // `channelCount <= 0` early return below mounts a different root
    // element, so the scroller ref the listener was holding is stale.
  }, [coordinator, channelCount, isSingleChannel]);

  // Keyboard accessibility — the same coordinator path the IO callback
  // uses, so the visual + audio + subscription state stay consistent.
  useEffect(() => {
    if (isSingleChannel) return;
    const scroller = scrollerRef.current;
    if (!scroller) return;
    const handler = (event: KeyboardEvent) => {
      if (event.key === "ArrowDown" || event.key === "PageDown") {
        event.preventDefault();
        if (dispatchInFlightRef.current) return;
        dispatchInFlightRef.current = true;
        coordinator.swipeNext();
        setActiveVirtual((v) => v + 1);
      } else if (event.key === "ArrowUp" || event.key === "PageUp") {
        event.preventDefault();
        if (dispatchInFlightRef.current) return;
        dispatchInFlightRef.current = true;
        coordinator.swipePrev();
        setActiveVirtual((v) => v - 1);
      }
    };
    scroller.addEventListener("keydown", handler);
    return () => {
      scroller.removeEventListener("keydown", handler);
    };
  }, [coordinator, channelCount, isSingleChannel]);

  if (channelCount <= 0) {
    return (
      <div ref={scrollerRef} className="feed-pager fixed inset-0 z-40 bg-black" aria-busy="true" />
    );
  }

  const setCellRef = (ord: CellOrdinal) => (el: HTMLDivElement | null) => {
    cellRefs.current[ord] = el;
  };

  return (
    <div
      ref={scrollerRef}
      tabIndex={0}
      role="feed"
      aria-label="Live channel feed"
      className={
        isSingleChannel
          ? "feed-pager fixed inset-0 z-40 overflow-hidden bg-black focus:outline-none"
          : "feed-pager fixed inset-0 z-40 overflow-y-scroll bg-black focus:outline-none"
      }
      style={
        isSingleChannel
          ? {
              // Single channel — no swipe target, no snap container.
              // `touch-action: none` blocks pan gestures so iOS Safari
              // doesn't show a rubber-band on attempted drags, which
              // otherwise reveals the black void behind the fixed root.
              overscrollBehaviorY: "contain",
              touchAction: "none",
            }
          : {
              scrollSnapType: "y mandatory",
              scrollBehavior: "auto",
              WebkitOverflowScrolling: "touch",
              overscrollBehaviorY: "contain",
              touchAction: "pan-y",
            }
      }
    >
      {cells.map((cell) => {
        const isActive = cell.ord === CENTER_ORD;
        // Bind audio + the mute toggle only on the active cell so
        // neighbors stay silent and the click handler on the
        // surrounding tile doesn't fire when the user is just
        // checking the next channel.
        // Suppress the "Tap to unmute" pill while there's no live video
        // track on the active cell: the BootstrapOverlay is the surface
        // talking to the user during cold-start / warmup, and stacking
        // an unmute affordance over a placeholder MP4 (which has no
        // audio) reads as broken UI.
        const showUnmutePill =
          !HIDE_VIDEO_UI && isActive && activeTrack !== null && (audioMuted || !canPlaybackAudio);
        // Only the active cell can be in product-focus mode — neighbors
        // always render the seller full-bleed because they're not
        // subscribed to a video track anyway. Focus state is driven by
        // the `focusedProduct` snapshot, not the live `pinnedProduct`,
        // so dismiss / new-show events arriving while the user is
        // browsing don't tear down the detail surface they opened.
        const cellInFocus = !HIDE_VIDEO_UI && isActive && focusedProduct != null;
        return (
          <div
            // Keying by the stable cell ordinal (not `virtualKey`) is
            // intentional: it's how the same three DOM nodes — and the
            // same three `<LiveRoomPlayer>` mounts — survive across
            // commits. `data-virtual-key` is the source-of-truth for
            // tests and devtools.
            key={cell.ord}
            ref={setCellRef(cell.ord)}
            data-cell-ord={cell.ord}
            data-channel-ordinal={cell.channelOrdinal}
            data-virtual-key={cell.virtualKey}
            data-product-in-focus={cellInFocus || undefined}
            className={
              isSingleChannel
                ? "relative h-[100dvh] w-full"
                : "snap-start snap-always relative h-[100dvh] w-full"
            }
          >
            {/* Ambient blurred backdrop. Sits behind the 9:16 column so
                the desktop letterbox bars inherit the seller's color
                instead of reading as dead black. Mounts on every cell
                so the first frame already has a tint; the live track
                crossfades the blur once it subscribes on the active
                cell. */}
            <BlurredBackdrop videoTrack={isActive ? activeTrack : null} />

            {/* Letterbox column. The outer cell paints the surrounding
                bars (via the BlurredBackdrop above + the cell's own
                background); the inner column is a 9:16 viewport that
                matches Whatnot/TikTok/Reels' web layout. On mobile
                portrait the viewport's width is narrower than
                `100dvh * 9/16`, so the clamp never fires and the column
                fills edge-to-edge (no bars). On a desktop window the
                clamp pins the column to a phone-shaped frame centered
                horizontally. Overlays (pinned card, unmute pill, PiP)
                live inside this column so they hug the video, never
                the letterbox. */}
            <div className="relative mx-auto h-full w-full max-w-[calc(100dvh*9/16)]">
              {cellInFocus && focusedProduct ? (
                <>
                  {/* Product fills the cell; seller PiPs into the corner.
                    The full-bleed LiveRoomPlayer is replaced by a small
                    one inside `DraggablePiP` — audio stays bound on the
                    PiP element, so the persona keeps speaking. */}
                  <ProductFocusView product={focusedProduct} onClose={enterAvatarMode} />
                  <DraggablePiP
                    width={sellerPipSize.w}
                    height={sellerPipSize.h}
                    // Bottom-right keeps the seller visible without
                    // covering the central product photography or top-
                    // anchored close button. `insetBottom` only needs to
                    // clear the action card (~143px: title + Buy button
                    // + padding) plus the pagination dots (~18px) — the
                    // previous 210px figure left so much padding the PiP
                    // floated at the viewport's vertical center on mobile
                    // rather than sitting in the bottom corner. Matches
                    // DraggablePiP's default `insetBottom` for tall pinned-
                    // card layouts.
                    initialCorner="bottom-right"
                    insetBottom={160}
                    ariaLabel="Maximize avatar"
                    onTap={enterAvatarMode}
                  >
                    <div className="relative h-full w-full overflow-hidden rounded-2xl bg-black shadow-[0_8px_24px_rgba(0,0,0,0.45)] ring-1 ring-white/15">
                      <LiveRoomPlayer
                        videoTrack={activeTrack}
                        audioTrack={activeAudioTrack}
                        muted={audioMuted}
                        canPlaybackAudio={canPlaybackAudio}
                        onToggleMute={handleToggleAudio}
                        isPip
                        className="h-full w-full"
                      />
                    </div>
                  </DraggablePiP>
                </>
              ) : (
                <>
                  <LiveRoomPlayer
                    videoTrack={isActive ? activeTrack : null}
                    audioTrack={isActive ? activeAudioTrack : null}
                    muted={isActive ? audioMuted : true}
                    canPlaybackAudio={isActive ? canPlaybackAudio : true}
                    onToggleMute={isActive ? handleToggleAudio : undefined}
                    className="h-full w-full"
                  />
                  {showUnmutePill && (
                    <button
                      type="button"
                      onClick={(event) => {
                        event.stopPropagation();
                        handleToggleAudio();
                      }}
                      aria-label="Tap to unmute"
                      className="pointer-events-auto absolute left-1/2 top-6 z-30 flex -translate-x-1/2 items-center gap-2 rounded-full bg-black/65 px-4 py-2 text-sm font-medium text-white shadow-[0_2px_8px_rgba(0,0,0,0.45)] backdrop-blur transition active:scale-95"
                    >
                      <Volume2 size={16} strokeWidth={2.2} />
                      <span>Tap to unmute</span>
                    </button>
                  )}
                  {!HIDE_VIDEO_UI && isActive && pinnedProduct && (
                    <div className="pointer-events-auto absolute inset-x-3 bottom-3 z-30 md:inset-x-6 md:bottom-6">
                      <PinnedProductCard product={pinnedProduct} onExpand={enterProductMode} />
                    </div>
                  )}
                  {/* Bootstrap overlay sits on top of everything else on
                    the active cell whenever there's no live video track:
                    cold-start ("Sell me stuff" CTA), warmup (loading
                    card), or error (retry). Hides itself when a track
                    binds — the prop drives that, so this is a one-line
                    consumer with no state of its own. Mounted *outside*
                    the unmute / pinned-card branches because it
                    supersedes both: there is no audio to unmute and the
                    pinned card is stale until live frames arrive. */}
                  {isActive && <BootstrapOverlay hasActiveTrack={activeTrack !== null} />}
                </>
              )}
              {/* Global chat overlay — single instance per active cell,
                rendered OUTSIDE the avatar/product ternary so the same
                React node (and therefore the same `GlobalChatStore` and
                its rolling buffer) survives the mode switch. The
                wrapper div's class/style varies by mode (z-index above
                the focus surface in product mode, sitting above the
                pinned card in avatar mode); React just patches those
                on the existing element rather than remounting. Without
                this hoist, each ternary branch mounts its own overlay
                and the chat appears to restart on every toggle.

                In product-focus mode the chat switches to its compact
                density (3 messages, slimmer composer) AND moves a tier
                higher (`bottom: 12rem` vs `10rem`) so the composer pill
                no longer crowds the gallery's pagination dots, and the
                shorter message stack stops obscuring the gallery's
                instructional copy. The product is the hero in this
                surface — the chat recedes to an ambient peek. */}
              {!HIDE_VIDEO_UI && isActive && (
                <div
                  className={
                    cellInFocus
                      ? "pointer-events-none absolute left-3 z-30 md:left-6"
                      : "pointer-events-none absolute left-3 z-20 md:left-6"
                  }
                  style={{
                    bottom: cellInFocus ? "12rem" : pinnedProduct ? "8.5rem" : "1rem",
                  }}
                >
                  <GlobalChatOverlay
                    productId={pinnedProduct?.productId ?? null}
                    chatBridge={chatBridge}
                    density={cellInFocus ? "compact" : "full"}
                  />
                </div>
              )}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/**
 * Project a bootstrap `currentAd` snapshot onto the same `PinnedProduct`
 * shape the text-stream `show` events produce, so the seed effect can
 * pin it without a separate render path.
 */
function pinnedFromCurrentAd(ad: ChannelCurrentAd): PinnedProduct {
  // `media` arrives from the server already filtered to image kind +
  // ordered by position; project straight through so the focus view
  // can paint pagination dots and preload every slide on first render
  // instead of waiting for the agent's `product_pitch.show` text-stream.
  const media: PinnedProductMedia[] = (ad.product.media ?? []).map((m) => ({
    position: m.position,
    kind: m.kind === "video" ? "video" : "image",
    url: m.url,
  }));
  return {
    productId: ad.product.id,
    title: ad.product.title,
    imageUrl: ad.product.imageUrl ?? undefined,
    media: media.length > 0 ? media : undefined,
    priceCents: ad.product.priceCents ?? undefined,
    currency: ad.product.currency,
    deepLinkUrl: ad.product.deepLinkUrl ?? undefined,
    landingUrl: ad.product.landingUrl ?? undefined,
    pinnedAt: Date.now(),
  };
}

/**
 * Project the wire-side `media` array onto the typed `PinnedProductMedia`
 * shape, dropping anything with an unrecognised `kind` so the gallery
 * renderer never has to branch on string contents at render time.
 */
function normalizeMedia(raw: PitchPopupMediaItem[] | undefined): PinnedProductMedia[] | undefined {
  if (!raw || raw.length === 0) return undefined;
  const out: PinnedProductMedia[] = [];
  for (const item of raw) {
    if (item.kind !== "image" && item.kind !== "video") continue;
    if (typeof item.position !== "number" || typeof item.url !== "string") continue;
    out.push({ position: item.position, kind: item.kind, url: item.url });
  }
  out.sort((a, b) => a.position - b.position);
  return out.length ? out : undefined;
}
