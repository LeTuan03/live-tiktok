"use client";

// The on-air screen: the broadcast as viewers see it, full screen and 9:16.
//
// Meant for viewers and as an OBS Studio "Browser" source to restream to
// TikTok Live or YouTube Live. It follows the control room through the
// broadcast event stream and plays the derived timeline itself, so every open
// screen shows the same video at the same second. `?clean=1` hides the
// overlays (live badge, product card) for a bare video feed.

import { Radio, Volume2 } from "lucide-react";
import { useSearchParams } from "next/navigation";
import { type ReactNode, useMemo, useState } from "react";
import { ApiError } from "@/lib/api";
import { useBroadcast, useNow } from "@/lib/broadcast/hooks";
import { buildTimeline, isOnAir, itemsById, positionAt } from "@/lib/broadcast/logic";
import { formatPrice } from "@/lib/studio/logic";
import { BroadcastPlayer } from "./BroadcastPlayer";

export function OnAirView({ broadcastId }: { broadcastId: string }) {
  const params = useSearchParams();
  const clean = params?.get("clean") === "1";
  const query = useBroadcast(broadcastId);
  const broadcast = query.data;
  const now = useNow(500);
  const [muted, setMuted] = useState(false);
  const [blocked, setBlocked] = useState(false);

  const timeline = useMemo(() => (broadcast ? buildTimeline(broadcast) : null), [broadcast]);
  const items = useMemo(() => (broadcast ? itemsById(broadcast) : new Map()), [broadcast]);

  if (!broadcast || !timeline) {
    const missing = query.error instanceof ApiError && query.error.status === 404;
    return (
      <Screen>
        <Message
          title={missing ? "Không tìm thấy phiên livestream" : query.isError ? "Mất kết nối" : ""}
        />
      </Screen>
    );
  }

  const onAir = isOnAir(broadcast.playback.status);
  const playhead = onAir ? broadcast.playback.playhead : null;
  const position = positionAt(timeline, playhead, broadcast.clockOffsetMs, now);
  const item = position ? items.get(position.itemId) : undefined;
  const product = position?.phase === "body" ? item?.product : null;

  return (
    <Screen>
      <BroadcastPlayer
        timeline={timeline}
        items={items}
        playhead={playhead}
        clockOffsetMs={broadcast.clockOffsetMs}
        muted={muted}
        onSoundBlocked={() => {
          setMuted(true);
          setBlocked(true);
        }}
        className="h-full max-h-[100dvh] w-full max-w-[calc(100dvh*9/16)]"
      >
        {!position && (
          <Message
            title={broadcast.title}
            body={
              broadcast.playback.status === "ended"
                ? "Phiên livestream đã kết thúc. Cảm ơn bạn đã xem!"
                : "Phiên livestream sắp bắt đầu."
            }
          />
        )}
        {!clean && position && (
          <div className="pointer-events-none absolute inset-x-0 top-0 flex items-center gap-2 bg-gradient-to-b from-black/50 to-transparent p-3">
            <span
              className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-xs font-bold text-white ${
                playhead?.playing ? "bg-red-600" : "bg-neutral-600"
              }`}
            >
              <Radio className="h-3.5 w-3.5" /> {playhead?.playing ? "LIVE" : "TẠM DỪNG"}
            </span>
            <span className="truncate text-sm font-semibold text-white drop-shadow">
              {broadcast.title}
            </span>
          </div>
        )}
        {!clean && product && (
          <div
            className="pointer-events-none absolute inset-x-3 bottom-3 flex items-center gap-3 rounded-2xl bg-white/95 p-2.5 text-neutral-900 shadow-lg"
            data-testid="OnAir.product"
          >
            {product.imageUrl && (
              <img
                src={product.imageUrl}
                alt=""
                className="h-14 w-14 shrink-0 rounded-xl object-cover"
              />
            )}
            <div className="min-w-0 flex-1">
              <p className="truncate text-sm font-semibold">{product.name}</p>
              {product.price !== null && (
                <p className="text-base font-bold text-red-600">
                  {formatPrice(product.price, product.currency)}
                </p>
              )}
            </div>
            <span className="shrink-0 rounded-full bg-red-600 px-3 py-1.5 text-xs font-bold text-white">
              Mua ngay
            </span>
          </div>
        )}
        {blocked && muted && (
          <button
            type="button"
            onClick={() => {
              setMuted(false);
              setBlocked(false);
            }}
            className="absolute left-1/2 top-1/2 inline-flex -translate-x-1/2 -translate-y-1/2 items-center gap-2 rounded-full bg-black/70 px-5 py-3 text-sm font-semibold text-white"
          >
            <Volume2 className="h-5 w-5" /> Bấm để bật tiếng
          </button>
        )}
      </BroadcastPlayer>
    </Screen>
  );
}

function Screen({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-[100dvh] w-full items-center justify-center overflow-hidden bg-black">
      {children}
    </div>
  );
}

function Message({ title, body }: { title: string; body?: string }) {
  return (
    <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-8 text-center text-white">
      {title && <p className="text-lg font-semibold">{title}</p>}
      {body && <p className="text-sm text-white/70">{body}</p>}
    </div>
  );
}
