"use client";

// The control desk: preview monitor, transport buttons, what is on air now
// and what comes next. On air it shows and drives the server's playback;
// otherwise it can run a local preview ("Xem thử") of the whole playlist.

import {
  CircleStop,
  Pause,
  Play,
  Radio,
  SkipBack,
  SkipForward,
  Square,
  Volume2,
  VolumeX,
} from "lucide-react";
import type { ReactNode } from "react";
import { cycleLabel, formatClock, nextItem } from "@/lib/broadcast/logic";
import type { Playhead, Position, Timeline } from "@/lib/broadcast/timeline";
import type { Broadcast, BroadcastItem } from "@/lib/broadcast/types";
import { formatPrice } from "@/lib/studio/logic";
import { Button } from "@/components/studio/ui";
import { BroadcastPlayer } from "./BroadcastPlayer";

export type DeskMode = "onair" | "preview" | "idle";

export interface ControlDeskProps {
  broadcast: Broadcast;
  timeline: Timeline;
  items: Map<string, BroadcastItem>;
  mode: DeskMode;
  playhead: Playhead | null;
  clockOffsetMs: number;
  position: Position | null;
  nowMs: number;
  muted: boolean;
  onMute: (muted: boolean) => void;
  busy: boolean;
  onStart: () => void;
  onPreview: () => void;
  onStopPreview: () => void;
  /** pause, resume, next, prev, finish, stop: on air or in the preview. */
  onAction: (action: "pause" | "resume" | "next" | "prev" | "finish" | "stop") => void;
}

export function ControlDesk(props: ControlDeskProps) {
  const { broadcast, timeline, items, mode, playhead, position, muted } = props;
  const current = position ? (items.get(position.itemId) ?? null) : null;
  const upcoming = nextItem(timeline, position, items);
  const playing = !!playhead?.playing;
  const canStart = timeline.items.length > 0;

  return (
    <div className="flex flex-col gap-3">
      <div className="mx-auto w-full max-w-[18rem] lg:max-w-[22rem]">
        <BroadcastPlayer
          timeline={timeline}
          items={items}
          playhead={playhead}
          clockOffsetMs={props.clockOffsetMs}
          muted={muted}
          onSoundBlocked={() => props.onMute(true)}
          className="aspect-[9/16] w-full rounded-2xl border border-studio-border"
        >
          <MonitorBadges mode={mode} playing={playing} />
          {!position && (
            <div className="absolute inset-0 flex flex-col items-center justify-center gap-2 p-6 text-center">
              <p className="text-sm font-semibold text-studio-fg">{broadcast.title}</p>
              <p className="text-xs text-studio-muted">
                {broadcast.playback.status === "ended"
                  ? "Phiên đã kết thúc."
                  : canStart
                    ? "Bấm Bắt đầu để lên sóng, hoặc Xem thử để duyệt lại."
                    : "Thêm video sản phẩm để bắt đầu."}
              </p>
            </div>
          )}
          <button
            type="button"
            onClick={() => props.onMute(!muted)}
            aria-label={muted ? "Bật tiếng" : "Tắt tiếng"}
            className="absolute bottom-2 right-2 rounded-full bg-black/60 p-2 text-white hover:bg-black/80"
          >
            {muted ? <VolumeX className="h-4 w-4" /> : <Volume2 className="h-4 w-4" />}
          </button>
        </BroadcastPlayer>
      </div>

      <Transport {...props} playing={playing} canStart={canStart} />

      {current && position && (
        <NowPlaying
          broadcast={broadcast}
          item={current}
          position={position}
          upcoming={upcoming}
          mode={mode}
          nowMs={props.nowMs}
        />
      )}
    </div>
  );
}

function MonitorBadges({ mode, playing }: { mode: DeskMode; playing: boolean }) {
  if (mode === "idle") return null;
  return (
    <div className="absolute left-2 top-2 flex gap-1.5">
      {mode === "onair" ? (
        <span
          className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-bold text-white ${
            playing ? "bg-red-600" : "bg-neutral-600"
          }`}
        >
          <Radio className="h-3 w-3" /> {playing ? "LIVE" : "TẠM DỪNG"}
        </span>
      ) : (
        <span className="rounded-md bg-amber px-2 py-0.5 text-[11px] font-bold text-amber-fg">
          XEM THỬ{playing ? "" : " · TẠM DỪNG"}
        </span>
      )}
    </div>
  );
}

function Transport({
  mode,
  busy,
  playing,
  canStart,
  onStart,
  onPreview,
  onStopPreview,
  onAction,
  broadcast,
}: ControlDeskProps & { playing: boolean; canStart: boolean }) {
  if (mode === "idle") {
    return (
      <div className="grid grid-cols-2 gap-2">
        <Button
          variant="live"
          size="lg"
          disabled={!canStart}
          busy={busy}
          onClick={onStart}
          data-testid="Desk.start"
          className="col-span-2"
        >
          <Radio className="h-5 w-5" />
          {broadcast.playback.status === "ended" ? "Phát lại từ đầu" : "Bắt đầu livestream"}
        </Button>
        <Button
          disabled={!canStart}
          onClick={onPreview}
          className="col-span-2"
          data-testid="Desk.preview"
        >
          <Play className="h-4 w-4" /> Xem thử toàn bộ danh sách
        </Button>
      </div>
    );
  }
  const finishLabel = broadcast.outro ? "Kết thúc (phát video kết thúc)" : "Kết thúc phiên";
  return (
    <div className="flex flex-col gap-2">
      <div className="grid grid-cols-3 gap-2">
        <TransportButton label="Video trước" onClick={() => onAction("prev")} busy={busy}>
          <SkipBack className="h-5 w-5" />
        </TransportButton>
        <TransportButton
          label={playing ? "Tạm dừng" : "Tiếp tục"}
          onClick={() => onAction(playing ? "pause" : "resume")}
          busy={busy}
          primary
          testId="Desk.toggle"
        >
          {playing ? <Pause className="h-5 w-5" /> : <Play className="h-5 w-5" />}
        </TransportButton>
        <TransportButton
          label="Video tiếp theo"
          onClick={() => onAction("next")}
          busy={busy}
          testId="Desk.next"
        >
          <SkipForward className="h-5 w-5" />
        </TransportButton>
      </div>
      {mode === "onair" ? (
        <div className="grid grid-cols-2 gap-2">
          <Button
            size="sm"
            disabled={busy}
            onClick={() => {
              if (window.confirm("Kết thúc phiên livestream?")) onAction("finish");
            }}
            data-testid="Desk.finish"
          >
            <Square className="h-3.5 w-3.5" /> {finishLabel}
          </Button>
          <Button
            size="sm"
            variant="danger"
            disabled={busy}
            onClick={() => {
              if (window.confirm("Dừng phiên ngay lập tức?")) onAction("stop");
            }}
            data-testid="Desk.stop"
          >
            <CircleStop className="h-3.5 w-3.5" /> Dừng ngay
          </Button>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-2">
          <Button size="sm" onClick={onStopPreview}>
            <Square className="h-3.5 w-3.5" /> Thoát xem thử
          </Button>
          <Button size="sm" variant="live" disabled={!canStart} busy={busy} onClick={onStart}>
            <Radio className="h-3.5 w-3.5" /> Lên sóng
          </Button>
        </div>
      )}
    </div>
  );
}

function TransportButton({
  label,
  onClick,
  busy,
  primary = false,
  testId,
  children,
}: {
  label: string;
  onClick: () => void;
  busy: boolean;
  primary?: boolean;
  testId?: string;
  children: ReactNode;
}) {
  return (
    <Button
      variant={primary ? "primary" : "ghost"}
      size="lg"
      onClick={onClick}
      disabled={busy}
      aria-label={label}
      title={label}
      data-testid={testId}
      className="flex-col gap-1 py-2.5"
    >
      {children}
      <span className="text-[11px] font-medium">{label}</span>
    </Button>
  );
}

function NowPlaying({
  broadcast,
  item,
  position,
  upcoming,
  mode,
  nowMs,
}: {
  broadcast: Broadcast;
  item: BroadcastItem;
  position: Position;
  upcoming: BroadcastItem | null;
  mode: DeskMode;
  nowMs: number;
}) {
  const product = item.product;
  const image = product?.imageUrl ?? item.thumbUrl;
  const fraction = position.lengthMs ? position.offsetMs / position.lengthMs : 0;
  const startedAt = broadcast.playback.startedAt ? Date.parse(broadcast.playback.startedAt) : null;
  const heading =
    position.phase === "intro"
      ? "Đang phát video mở đầu"
      : position.phase === "outro"
        ? "Đang phát video kết thúc"
        : "Đang giới thiệu";
  return (
    <section
      className="rounded-2xl border border-studio-border bg-studio-panel p-3"
      data-testid="Desk.nowPlaying"
    >
      <div className="flex gap-3">
        <div className="h-16 w-16 shrink-0 overflow-hidden rounded-lg bg-studio-hover">
          {image && <img src={image} alt="" className="h-full w-full object-cover" />}
        </div>
        <div className="min-w-0 flex-1">
          <p className="text-[11px] font-medium uppercase tracking-wide text-live">{heading}</p>
          <p className="truncate text-sm font-semibold">{product?.name ?? item.title}</p>
          {product && product.price !== null && (
            <p className="text-sm font-semibold text-amber">
              {formatPrice(product.price, product.currency)}
            </p>
          )}
        </div>
      </div>
      <div className="mt-3">
        <div
          className="h-1.5 overflow-hidden rounded-full bg-studio-hover"
          role="progressbar"
          aria-label="Tiến trình video"
          aria-valuemin={0}
          aria-valuemax={100}
          aria-valuenow={Math.round(fraction * 100)}
        >
          <div className="h-full bg-live" style={{ width: `${Math.min(fraction, 1) * 100}%` }} />
        </div>
        <div className="mt-1 flex justify-between text-xs tabular-nums text-studio-muted">
          <span>{formatClock(position.offsetMs)}</span>
          <span>{formatClock(position.lengthMs)}</span>
        </div>
      </div>
      <dl className="mt-2 grid grid-cols-2 gap-x-3 gap-y-1 text-xs">
        <dt className="text-studio-muted">Tiếp theo</dt>
        <dd className="truncate text-right">{upcoming ? upcoming.title : "Hết phiên"}</dd>
        {position.phase === "body" && (
          <>
            <dt className="text-studio-muted">Vòng lặp</dt>
            <dd className="text-right">{cycleLabel(position, broadcast)}</dd>
          </>
        )}
        {mode === "onair" && startedAt && (
          <>
            <dt className="text-studio-muted">Đã phát</dt>
            <dd className="text-right tabular-nums">{formatClock(nowMs - startedAt)}</dd>
          </>
        )}
      </dl>
    </section>
  );
}
