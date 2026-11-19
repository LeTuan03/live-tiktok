"use client";

// 9:16 player for the model's LiveKit video and audio tracks.
//
// Browsers block audio autoplay until the user interacts with the page, so
// the player shows an explicit unmute control whenever LiveKit reports that
// playback is blocked, and a mute toggle once sound is on.

import type { RemoteAudioTrack, RemoteVideoTrack } from "livekit-client";
import { Volume2, VolumeX } from "lucide-react";
import { type ReactNode, useEffect, useRef, useState } from "react";

export interface LiveVideoProps {
  videoTrack: RemoteVideoTrack | null;
  audioTrack: RemoteAudioTrack | null;
  canPlayAudio: boolean;
  onStartAudio: () => Promise<void> | void;
  /** Shown centered while there is no video track. */
  placeholder: ReactNode;
  /** Extra overlays (captions, product card, chat). */
  children?: ReactNode;
  className?: string;
}

export function LiveVideo({
  videoTrack,
  audioTrack,
  canPlayAudio,
  onStartAudio,
  placeholder,
  children,
  className = "",
}: LiveVideoProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const [muted, setMuted] = useState(false);

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
    if (audioRef.current) audioRef.current.muted = muted;
  }, [muted, audioTrack]);

  const blocked = audioTrack !== null && !canPlayAudio;
  const hasVideo = videoTrack !== null;

  return (
    <div
      data-testid="LiveVideo.root"
      data-has-video={hasVideo}
      className={`relative overflow-hidden bg-black ${className}`}
    >
      <video
        ref={videoRef}
        autoPlay
        playsInline
        muted
        className={`h-full w-full object-cover ${hasVideo ? "" : "invisible"}`}
      />
      {/* biome-ignore lint/a11y/useMediaCaption: live speech, captions come from the agent state */}
      <audio ref={audioRef} autoPlay />
      {!hasVideo && (
        <div className="absolute inset-0 flex items-center justify-center p-6 text-center">
          {placeholder}
        </div>
      )}
      {children}
      {hasVideo && blocked && (
        <button
          type="button"
          data-testid="LiveVideo.unmute"
          onClick={() => {
            setMuted(false);
            void onStartAudio();
          }}
          className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center gap-2 rounded-full bg-white px-5 py-3 text-sm font-semibold text-black shadow-lg"
        >
          <VolumeX className="h-4 w-4" />
          Chạm để bật tiếng
        </button>
      )}
      {hasVideo && audioTrack && !blocked && (
        <button
          type="button"
          aria-label={muted ? "Bật tiếng" : "Tắt tiếng"}
          onClick={() => setMuted((m) => !m)}
          className="absolute right-3 top-3 flex h-9 w-9 items-center justify-center rounded-full bg-black/55 text-white backdrop-blur hover:bg-black/75"
        >
          {muted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
        </button>
      )}
    </div>
  );
}
