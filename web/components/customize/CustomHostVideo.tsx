"use client";

import { useEffect, useRef } from "react";
import { ChevronLeft, Volume2, VolumeX } from "lucide-react";
import type { RemoteTrack } from "livekit-client";
import type { HostPhase } from "./useCustomHostRoom";

interface CustomHostVideoProps {
  videoTrack: RemoteTrack | null;
  audioTrack: RemoteTrack | null;
  /** The user's uploaded portrait, shown as the still until frames arrive. */
  placeholderUrl: string | null;
  phase: HostPhase;
  muted: boolean;
  canPlaybackAudio: boolean;
  onToggleMute: () => void;
  onReset: () => void;
}

/**
 * The 9:16 broadcast monitor. Reuses the track-attach + placeholder-pulse +
 * connecting-pill patterns from `components/live/LiveRoomPlayer.tsx`, but
 * paints the user's *own* uploaded portrait as the still (not the feed's
 * default placeholder). The still renders whenever `placeholderUrl` is set —
 * including before go-live, so the monitor previews the host as the user
 * builds it — then crossfades to video and lights the green ON-AIR dot once
 * frames arrive.
 */
export function CustomHostVideo({
  videoTrack,
  audioTrack,
  placeholderUrl,
  phase,
  muted,
  canPlaybackAudio,
  onToggleMute,
  onReset,
}: CustomHostVideoProps) {
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
    audioTrack.attach(el);
    return () => {
      audioTrack.detach(el);
    };
  }, [audioTrack]);

  useEffect(() => {
    const audio = audioRef.current;
    if (!audio) return;
    audio.muted = muted;
    if (!muted) void audio.play().catch(() => {});
  }, [muted, audioTrack]);

  const showUnmuteHint = !!audioTrack && (muted || !canPlaybackAudio);
  const showConnecting = !videoTrack && (phase === "connecting" || phase === "idle");

  return (
    <div className="relative h-full w-full overflow-hidden rounded-2xl border border-studio-border bg-black">
      {placeholderUrl && (
        <img
          src={placeholderUrl}
          alt=""
          aria-hidden
          className={`pointer-events-none absolute inset-0 h-full w-full object-cover object-center transition-opacity duration-700 ${
            videoTrack ? "" : "animate-live-placeholder-pulse"
          }`}
        />
      )}
      <button
        type="button"
        aria-label={muted ? "Unmute audio" : "Mute audio"}
        onClick={onToggleMute}
        className="absolute inset-0 z-10 h-full w-full cursor-pointer"
      >
        <span className="sr-only">{muted ? "Unmute audio" : "Mute audio"}</span>
      </button>
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

      {/* Reset / edit — top-left, safe-area aware. The only way back on mobile. */}
      <button
        type="button"
        onClick={onReset}
        className="absolute left-3 top-3 z-30 inline-flex items-center gap-1 rounded-full bg-black/55 px-4 py-2 text-sm font-medium text-studio-fg ring-1 ring-white/15 backdrop-blur transition hover:ring-white/30 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-amber focus-visible:ring-offset-2 focus-visible:ring-offset-studio-bg"
        style={{ paddingTop: "max(0.5rem, env(safe-area-inset-top))" }}
      >
        <ChevronLeft size={16} strokeWidth={2.2} />
        Edit
      </button>

      {/* AI host disclosure — permanent, top-right (FTC). The word stays "AI Host"
          for compliance; the dot goes green + pulses once video is on air. */}
      <div className="pointer-events-none absolute right-3 top-3 z-30 inline-flex items-center gap-1.5 rounded-full bg-black/55 px-2.5 py-1 text-[11px] font-semibold uppercase tracking-wide text-studio-fg ring-1 ring-white/15 backdrop-blur">
        <span
          className={`h-1.5 w-1.5 rounded-full ${
            videoTrack ? "animate-live-pulse bg-live" : "bg-studio-faint"
          }`}
        />
        AI Host
      </div>

      {showConnecting && (
        <div className="pointer-events-none absolute left-1/2 top-6 z-20 flex -translate-x-1/2 items-center gap-2 rounded-full bg-black/55 px-4 py-2 text-sm font-medium text-studio-fg ring-1 ring-white/15 backdrop-blur">
          <span className="relative flex h-2 w-2">
            <span className="absolute inset-0 animate-ping rounded-full bg-studio-faint opacity-70" />
            <span className="relative h-2 w-2 rounded-full bg-studio-faint" />
          </span>
          <span>Going on air…</span>
        </div>
      )}

      {showUnmuteHint && (
        <div className="pointer-events-none absolute bottom-4 left-1/2 z-20 flex -translate-x-1/2 items-center gap-2 rounded-full bg-black/55 px-4 py-2 text-sm font-medium text-studio-fg ring-1 ring-white/15 backdrop-blur">
          <VolumeX size={15} />
          Tap to unmute
        </div>
      )}
      {!showUnmuteHint && videoTrack && !muted && (
        <div className="pointer-events-none absolute bottom-4 right-4 z-20 flex h-9 w-9 items-center justify-center rounded-full bg-black/55 text-studio-fg ring-1 ring-white/15 backdrop-blur">
          <Volume2 size={15} />
        </div>
      )}

      {(phase === "lost" || phase === "error") && (
        <div className="absolute inset-x-4 bottom-4 z-20 rounded-xl bg-black/55 px-4 py-3 text-center text-sm text-studio-fg ring-1 ring-white/15 backdrop-blur">
          {phase === "lost" ? "Reconnecting…" : "The stream dropped. Tap Edit to try again."}
        </div>
      )}
    </div>
  );
}
