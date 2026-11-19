"use client";

import { useEffect, useRef } from "react";
import type { RemoteAudioTrack, RemoteVideoTrack } from "livekit-client";

interface AvatarStageProps {
  videoTrack: RemoteVideoTrack | null;
  audioTrack: RemoteAudioTrack | null;
  /** When true, renders a tighter "corner panel" layout instead of full-bleed. */
  corner?: boolean;
  /** Status pill text shown when no video track is attached yet. */
  placeholderLabel: string;
}

/**
 * Renders the AI avatar's video + audio. Track attachment is imperative
 * (livekit-client owns the underlying `<video>`/`<audio>` element via
 * `attach()`/`detach()`), so we hold refs to the wrapper nodes and ask
 * livekit to manage children. Mobile stretches the avatar full-bleed;
 * desktop's corner-panel mode constrains it to a fixed-aspect tile.
 */
export function AvatarStage({
  videoTrack,
  audioTrack,
  corner = false,
  placeholderLabel,
}: AvatarStageProps) {
  const videoSlotRef = useRef<HTMLDivElement | null>(null);
  const audioSlotRef = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const slot = videoSlotRef.current;
    if (!slot || !videoTrack) return;
    const element = videoTrack.attach();
    element.classList.add("h-full", "w-full", "object-cover", "bg-black");
    element.setAttribute("playsinline", "true");
    element.setAttribute("autoplay", "true");
    element.setAttribute("disablepictureinpicture", "true");
    slot.appendChild(element);
    return () => {
      videoTrack.detach(element);
      element.remove();
    };
  }, [videoTrack]);

  useEffect(() => {
    const slot = audioSlotRef.current;
    if (!slot || !audioTrack) return;
    const element = audioTrack.attach();
    element.setAttribute("autoplay", "true");
    slot.appendChild(element);
    return () => {
      audioTrack.detach(element);
      element.remove();
    };
  }, [audioTrack]);

  return (
    <div
      className={
        corner
          ? "relative aspect-[3/4] w-full overflow-hidden rounded-xl border border-slate-800 bg-black shadow-lg"
          : "relative h-full w-full overflow-hidden bg-black"
      }
    >
      <div ref={videoSlotRef} className="absolute inset-0" />
      {!videoTrack && (
        <div className="absolute inset-0 flex items-center justify-center text-sm text-slate-300">
          {placeholderLabel}
        </div>
      )}
      <span className="absolute left-3 top-3 inline-flex items-center gap-1 rounded-full bg-black/60 px-2 py-1 text-xs font-medium text-white backdrop-blur">
        <span aria-hidden="true">●</span>
        AI avatar
      </span>
      <div ref={audioSlotRef} className="hidden" aria-hidden="true" />
    </div>
  );
}
