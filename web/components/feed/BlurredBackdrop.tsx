"use client";
// Ambient blurred backdrop behind the 9:16 letterbox column.
//
// On desktop / tablet the feed cell is wider than the 9:16 video column,
// so the area to either side of the column needs to render *something*
// — TikTok and Reels use solid black there, but solid black on a
// branded social-shopping feed reads as dead space. Instead we mirror
// the seller across the bars with a heavy blur, so the letterbox
// inherits the persona's color/vibe without competing with the foreground.
//
// Two layers stacked:
//   1. A bundled persona still — paints instantly on first frame so the
//      bars are never raw black, and stays as the ambient base layer
//      after the video binds.
//   2. The live LiveKit video track, attached to a second `<video>`
//      element with the same blur/scale. Crossfades in once the track
//      is subscribed so the bars track the persona's actual motion.
//
// The second `attach()` is element-scoped — LiveKit supports the same
// track attached to multiple elements and the browser's compositor
// shares one decoded frame across both renders, so the extra GPU cost
// is the blur shader, not a second decode.

import { useEffect, useRef } from "react";
import type { RemoteVideoTrack } from "livekit-client";

// Bundled in `web/public/personas/` so first paint has zero network
// dependency. This is a 540×960 cover-crop of the demo host's portrait,
// a calm placeholder tint behind every cell until a live track binds.
// Per-persona stills via the channel coordinator are a follow-up; until
// then every cell shares this one backdrop.
const DEFAULT_BACKDROP_SRC = "/personas/demo-host-9x16.png";

interface BlurredBackdropProps {
  videoTrack: RemoteVideoTrack | null;
  /** Override the bundled fallback when a per-channel persona still is
   *  available. Currently unused — placeholder for the follow-up. */
  fallbackImageUrl?: string;
}

export function BlurredBackdrop({
  videoTrack,
  fallbackImageUrl = DEFAULT_BACKDROP_SRC,
}: BlurredBackdropProps) {
  const videoRef = useRef<HTMLVideoElement | null>(null);

  useEffect(() => {
    const el = videoRef.current;
    if (!el || !videoTrack) return;
    videoTrack.attach(el);
    return () => {
      videoTrack.detach(el);
    };
  }, [videoTrack]);

  return (
    <div aria-hidden className="pointer-events-none absolute inset-0 overflow-hidden">
      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={fallbackImageUrl}
        alt=""
        className="absolute inset-0 h-full w-full scale-110 object-cover opacity-70 blur-3xl"
      />
      <video
        ref={videoRef}
        playsInline
        autoPlay
        muted
        disablePictureInPicture
        className={`absolute inset-0 h-full w-full scale-110 object-cover blur-3xl transition-opacity duration-700 ${
          videoTrack ? "opacity-70" : "opacity-0"
        }`}
      />
    </div>
  );
}
