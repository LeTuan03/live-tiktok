"use client";

import { useEffect, useRef, useState } from "react";
import type { KeyboardEvent, MouseEvent } from "react";
import type { RemoteTrack } from "livekit-client";
import { Maximize2, Volume2, VolumeX } from "lucide-react";
import { getCurrentAd, type CurrentAd } from "@/lib/api/channels";
import { HIDE_VIDEO_UI } from "@/lib/feed/flags";

const CURRENT_AD_POLL_MS = 5_000;

interface LiveRoomPlayerProps {
  videoTrack: RemoteTrack | null;
  audioTrack: RemoteTrack | null;
  className?: string;
  /** Controlled mute state. The element renders muted under this flag
   *  so LiveKit's initial `attach()` call inside `useEffect` succeeds
   *  under the browser's autoplay policy (muted autoplay is always
   *  allowed). */
  muted: boolean;
  /** Toggles the controlled mute state. Called inside a user-gesture
   *  click handler so the browser's transient activation flag covers
   *  the subsequent `audio.play()` call. The parent is responsible for
   *  invoking `room.startAudio()` on the unmute transition so LK's
   *  internal `canPlaybackAudio` flag flips back. */
  onToggleMute?: () => void;
  /** Mirrors `Room.canPlaybackAudio`. When false, we surface the unmute
   *  affordance even after the user has tapped, because LK still hasn't
   *  resumed playback. */
  canPlaybackAudio?: boolean;
  /** When true, treat the tile as a PiP: the whole surface fires
   *  `onTap` (maximize) and a small corner icon owns the mute toggle.
   *  When false, tapping anywhere on the video toggles mute — the
   *  standard short-form vertical-video unmute affordance. */
  isPip?: boolean;
  onTap?: () => void;
  /** Channel id when this player is the active subscription on the
   *  channel-ring feed. When set, the component polls
   *  `GET /channels/{channelId}/current` every 5s and renders an
   *  ad-metadata overlay (product, persona, advertiser, Shop CTA).
   *  Pass `null` / omit on non-active cells so neighbor players do not
   *  hit the endpoint and waste bandwidth. The future replacement is a
   *  LiveKit data-channel event from the agent — out of scope here. */
  channelId?: string | null;
}

export function LiveRoomPlayer({
  videoTrack,
  audioTrack,
  className,
  muted,
  onToggleMute,
  canPlaybackAudio = true,
  isPip = false,
  onTap,
  channelId,
}: LiveRoomPlayerProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);

  useEffect(() => {
    const el = videoRef.current;
    if (!el || !videoTrack) return;
    videoTrack.attach(el);
    return () => {
      videoTrack.detach(el);
    };
  }, [videoTrack]);

  useEffect(() => {
    const el = audioRef.current;
    if (!el || !audioTrack) return;
    // The element is rendered with `muted={muted}` so LK's `el.play()`
    // call inside attach() lands inside the autoplay-allowed branch.
    audioTrack.attach(el);
    return () => {
      audioTrack.detach(el);
    };
  }, [audioTrack]);

  // Mirror the controlled mute state imperatively. On the false→true
  // transition we also fire `play()` so the audio element resumes
  // playback inside the same task as the originating click — losing
  // user activation across React's render commit is unlikely but not
  // impossible on Safari, and the cost of calling `.play()` on an
  // already-playing element is nil.
  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.muted = muted;
    if (!muted) {
      const playResult = audio.play();
      if (playResult && typeof playResult.then === "function") {
        playResult.catch(() => {
          // Playback rejection here is expected when there's no audio
          // track yet; the next attach() will pick it up.
        });
      }
    }
  }, [muted, audioTrack]);

  const currentAd = useCurrentAd(channelId ?? null);

  // Container click — in non-PiP this toggles mute. In PiP we route it
  // to onTap (maximize) and let a separate corner button handle mute.
  const handleContainerClick = () => {
    if (isPip) {
      onTap?.();
    } else if (onToggleMute) {
      onToggleMute();
    }
  };

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Enter" && event.key !== " ") return;
    if (isPip ? !onTap : !onToggleMute) return;
    event.preventDefault();
    if (isPip) onTap?.();
    else onToggleMute?.();
  };

  const showCornerMute = !HIDE_VIDEO_UI && isPip && audioTrack && onToggleMute;
  const showExpandIndicator = !HIDE_VIDEO_UI && isPip;
  const showAdOverlay =
    !HIDE_VIDEO_UI && !isPip && currentAd !== null && currentAd.product !== null;

  const interactive = isPip ? !!onTap : !!onToggleMute;
  const ariaLabel = isPip ? "Maximize avatar" : muted ? "Unmute audio" : "Mute audio";

  return (
    <div
      role={interactive ? "button" : undefined}
      tabIndex={interactive ? 0 : undefined}
      aria-label={interactive ? ariaLabel : undefined}
      onClick={interactive ? handleContainerClick : undefined}
      onKeyDown={interactive ? handleKeyDown : undefined}
      className={`relative h-full w-full overflow-hidden bg-black ${
        interactive ? "cursor-pointer" : ""
      } ${className ?? ""}`}
    >
      {/* Static host backdrop, paints the 9:16 column with a calm
          still while the LiveKit track is unsubscribed
          (between channels, on first paint, or when the agent is
          offline). Stays mounted underneath the live video so the
          streaming frames composite directly over it with identical
          letterboxing.

          While there's no track, we dim + slightly blur + slow-pulse
          the still so it doesn't read as a frozen live frame — the
          studio-quality portrait is convincing enough that viewers
          would otherwise assume the stream had stalled. The transform
          fades back to identity as the live video opacity rises, so
          the swap from placeholder to live frames reads as a single
          smooth wake-up rather than two separate elements. */}
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src="/personas/demo-host-9x16.png"
        alt=""
        aria-hidden
        data-testid="host-background-placeholder"
        data-loading={videoTrack ? undefined : "true"}
        className={`pointer-events-none absolute inset-0 h-full w-full object-cover object-center transition-[filter,transform] duration-700 ${
          videoTrack
            ? ""
            : "scale-[1.02] brightness-[0.78] blur-[2px] animate-live-placeholder-pulse"
        }`}
      />
      <video
        ref={videoRef}
        playsInline
        autoPlay
        muted
        disablePictureInPicture
        className={`relative h-full w-full object-cover transition-opacity duration-500 ${
          videoTrack ? "opacity-100" : "opacity-0"
        }`}
      />
      <audio ref={audioRef} autoPlay muted={muted} />

      {/* Connecting pill — top-center, shown while there's no live
          track on a non-PiP surface. Occupies the same slot as "Tap
          to unmute" but the two are mutually exclusive (unmute needs
          a track, this hides the moment a track binds). Distinct from
          the BootstrapOverlay loading card by intent: the overlay
          owns the *session* state and CTAs, while this pill owns the
          *video-track* state on the surface itself — together they
          tell the viewer "we're working on it" both above the fold
          and on the avatar's own real estate. */}
      {!HIDE_VIDEO_UI && !isPip && !videoTrack && (
        <div
          aria-hidden
          data-testid="live-room-connecting-pill"
          className="pointer-events-none absolute left-1/2 top-6 z-30 flex -translate-x-1/2 items-center gap-2 rounded-full bg-black/65 px-4 py-2 text-sm font-medium text-white shadow-[0_2px_8px_rgba(0,0,0,0.45)] backdrop-blur"
        >
          <span className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-amber-300 opacity-70" />
            <span className="relative h-2 w-2 rounded-full bg-amber-400" />
          </span>
          <span>Connecting…</span>
        </div>
      )}

      {showAdOverlay && currentAd && <AdMetadataOverlay ad={currentAd} />}

      {showExpandIndicator && <CornerExpandIndicator />}

      {showCornerMute && (
        <CornerMuteButton
          muted={muted || !canPlaybackAudio}
          onClick={(e) => {
            e.stopPropagation();
            onToggleMute?.();
          }}
        />
      )}
    </div>
  );
}

/**
 * Visual-only "tap to maximize" affordance on the PiP. Sits at the top-
 * left (mute lives at top-right) and is `pointer-events-none` so the
 * surrounding DraggablePiP still owns the actual tap-to-flip gesture —
 * we want the indicator to read as "the whole tile is the button," not
 * a separate click target. Hidden under `HIDE_VIDEO_UI` to match the
 * mute button's visibility rules.
 */
function CornerExpandIndicator() {
  return (
    <span
      aria-hidden
      className="pointer-events-none absolute left-0 top-0 flex h-11 w-11 items-center justify-center"
    >
      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-black/55 text-white shadow-[0_1px_3px_rgba(0,0,0,0.4)] backdrop-blur">
        <Maximize2 size={14} strokeWidth={2.2} />
      </span>
    </span>
  );
}

/**
 * Poll `GET /channels/{channelId}/current` while `channelId` is truthy.
 * Returns the latest `CurrentAd` payload (or `null` while nothing has
 * loaded yet / between channels). The interval is cleared on unmount
 * and whenever `channelId` flips to null — neighbor cells in the pager
 * must not keep hitting the endpoint.
 */
function useCurrentAd(channelId: string | null): CurrentAd | null {
  const [ad, setAd] = useState<CurrentAd | null>(null);

  useEffect(() => {
    if (!channelId) {
      setAd(null);
      return;
    }

    let disposed = false;
    let fetching = false;
    const controller = new AbortController();

    const fetchOnce = async () => {
      // Skip ticks while an earlier request is still in flight. A stalled
      // request on a slow network could otherwise pile up duplicates,
      // since the 5s interval keeps firing regardless.
      if (fetching) return;
      fetching = true;
      try {
        const next = await getCurrentAd(channelId, controller.signal);
        if (disposed) return;
        setAd(next);
      } catch (err) {
        if (disposed) return;
        // AbortError surfaces during channel swaps + unmount — drop it.
        const name = (err as { name?: string } | null)?.name;
        if (name === "AbortError") return;
        // Other transient failures: keep the last known overlay rather
        // than flashing it away. The next 5s tick retries.
      } finally {
        fetching = false;
      }
    };

    void fetchOnce();
    const handle = window.setInterval(() => {
      void fetchOnce();
    }, CURRENT_AD_POLL_MS);

    return () => {
      disposed = true;
      controller.abort();
      window.clearInterval(handle);
    };
  }, [channelId]);

  return ad;
}

function AdMetadataOverlay({ ad }: { ad: CurrentAd }) {
  const product = ad.product;
  if (!product) return null;

  // Click opens a deep link with a web fallback. A `deep_link_url`
  // typically uses a custom scheme (e.g. `myapp://products/...`),
  // which desktop browsers handle as a launch-app prompt or no-op.
  // Prefer the http(s) `landing_url` on web; only fall through to the
  // deep link when nothing else is available.
  const isHttpUrl = (s: string | null | undefined): s is string =>
    typeof s === "string" && /^https?:\/\//i.test(s);
  const href = isHttpUrl(product.landingUrl)
    ? product.landingUrl
    : isHttpUrl(product.deepLinkUrl)
      ? product.deepLinkUrl
      : (product.landingUrl ?? product.deepLinkUrl ?? "#");
  const advertiser = ad.advertiser?.name ?? "";
  const persona = ad.persona;

  return (
    <div
      // Stop propagation so tapping the overlay doesn't toggle mute on
      // the surrounding container. The CTA is the only interactive
      // surface; the rest of the panel is informational.
      onClick={(event) => event.stopPropagation()}
      onKeyDown={(event) => event.stopPropagation()}
      role="region"
      aria-label="Current advertisement"
      data-testid="ad-metadata-overlay"
      className="pointer-events-auto absolute inset-x-3 bottom-3 z-20 flex items-center gap-3 rounded-2xl bg-neutral-ink/85 p-3 text-white shadow-lg ring-1 ring-white/10 backdrop-blur md:inset-x-6 md:bottom-6 md:max-w-md"
    >
      {persona?.avatarUrl ? (
        // eslint-disable-next-line @next/next/no-img-element
        <img
          src={persona.avatarUrl}
          alt=""
          className="h-10 w-10 flex-shrink-0 rounded-full object-cover ring-2 ring-white/20"
        />
      ) : (
        <div aria-hidden className="h-10 w-10 flex-shrink-0 rounded-full bg-white/10" />
      )}
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-semibold">{product.title}</div>
        {(advertiser || persona?.displayName) && (
          <div className="truncate text-xs text-white/70">
            {persona?.displayName ? `${persona.displayName} · ` : ""}
            {advertiser}
          </div>
        )}
      </div>
      <a
        href={href}
        target="_blank"
        rel="noopener noreferrer"
        data-testid="ad-shop-cta"
        className="flex-shrink-0 rounded-full bg-brand-gradient px-4 py-2 text-xs font-semibold text-white shadow ring-1 ring-white/10 transition hover:opacity-90"
      >
        Shop
      </a>
    </div>
  );
}

function CornerMuteButton({
  muted,
  onClick,
}: {
  muted: boolean;
  onClick: (e: MouseEvent<HTMLButtonElement>) => void;
}) {
  // 22pt visible glyph inside a 44pt hit area (HIG floor) — the
  // standard pattern for controls inside a small overlay. The pointer
  // events must be stopped *here*, not just the click: the parent
  // `DraggablePiP` listens for `pointerdown` and calls
  // `setPointerCapture` on its own element, which redirects the
  // pointer-up away from the button and the implicit click never
  // fires. Halting `pointerdown`/`pointerup` propagation keeps the
  // pointer with the button, so the surrounding "tap to maximize"
  // gesture stays silent on a mute tap.
  return (
    <button
      type="button"
      aria-label={muted ? "Unmute audio" : "Mute audio"}
      onPointerDown={(e) => e.stopPropagation()}
      onPointerUp={(e) => e.stopPropagation()}
      onClick={onClick}
      className="absolute right-0 top-0 flex h-11 w-11 items-center justify-center"
    >
      <span className="flex h-7 w-7 items-center justify-center rounded-full bg-black/55 text-white shadow-[0_1px_3px_rgba(0,0,0,0.4)] backdrop-blur">
        {muted ? <VolumeX size={14} strokeWidth={2.2} /> : <Volume2 size={14} strokeWidth={2.2} />}
      </span>
    </button>
  );
}
