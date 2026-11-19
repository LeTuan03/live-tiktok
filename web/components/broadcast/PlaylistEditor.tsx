"use client";

// The session's playlist: an optional intro, the product videos in play
// order (drag and drop or up/down to reorder, time on air per video), an
// optional outro, and the loop setting. Edits are allowed on air: the server
// keeps the current video playing from the same second.

import {
  ArrowDown,
  ArrowUp,
  GripVertical,
  InfinityIcon,
  Play,
  Plus,
  Repeat,
  Trash2,
} from "lucide-react";
import { type ReactNode, useState } from "react";
import {
  addItems,
  removeItem,
  reorderItems,
  updateBroadcast,
  updateItem,
} from "@/lib/broadcast/api";
import { durationChoices, formatSpan, playsInSlot } from "@/lib/broadcast/logic";
import type { Broadcast, BroadcastItem, ItemRole, NewItem } from "@/lib/broadcast/types";
import { formatPrice, moveById, moveItem } from "@/lib/studio/logic";
import { Badge, Button, inputClass, Panel } from "@/components/studio/ui";
import { VideoPicker } from "./VideoPicker";

/** `inputClass` without its full width, for inline controls. */
const compactInput = inputClass.replace("w-full ", "");

export type Edit = (
  label: string,
  run: () => Promise<Broadcast>,
  optimistic?: (prev: Broadcast) => Broadcast,
) => Promise<Broadcast | undefined>;

export interface PlaylistEditorProps {
  broadcast: Broadcast;
  /** Item on screen right now (on air or in the preview), with its progress 0 to 1. */
  currentId: string | null;
  progress: number;
  /** Label of the per-row play button: jump on air, or preview from here. */
  playLabel: string;
  onPlay: (itemId: string) => void;
  edit: Edit;
  busy: boolean;
}

export function PlaylistEditor({
  broadcast,
  currentId,
  progress,
  playLabel,
  onPlay,
  edit,
  busy,
}: PlaylistEditorProps) {
  const id = broadcast.id;
  const items = broadcast.items;
  const [picker, setPicker] = useState<ItemRole | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);

  const reorder = (next: BroadcastItem[]) => {
    if (next.every((item, i) => item.id === items[i]?.id)) return;
    void edit(
      "Không đổi được thứ tự",
      () =>
        reorderItems(
          id,
          next.map((item) => item.id),
        ),
      (prev) => ({ ...prev, items: next }),
    );
  };

  const add = async (newItems: NewItem[]) => {
    const done = await edit("Không thêm được video", () => addItems(id, newItems));
    if (done) setPicker(null);
  };

  const remove = (item: BroadcastItem) => {
    if (
      item.id === currentId &&
      !window.confirm(`"${item.title}" đang phát. Gỡ khỏi phiên và chuyển sang video kế tiếp?`)
    ) {
      return;
    }
    void edit("Không gỡ được video", () => removeItem(id, item.id));
  };

  const setDuration = (item: BroadcastItem, raw: string) => {
    let value: number | null = raw === "" ? null : Number(raw);
    if (raw === "custom") {
      const answer = window.prompt("Phát video này bao nhiêu giây? (5 đến 3600)", "60");
      if (answer === null) return;
      value = Math.round(Number(answer));
      if (!Number.isFinite(value) || value < 5 || value > 3600) {
        window.alert("Thời lượng phải từ 5 đến 3600 giây.");
        return;
      }
    }
    void edit("Không đổi được thời lượng", () => updateItem(id, item.id, { durationS: value }));
  };

  return (
    <Panel
      title={`Danh sách phát (${items.length} video sản phẩm)`}
      action={
        <Button
          variant="primary"
          size="sm"
          onClick={() => setPicker("product")}
          data-testid="Playlist.add"
        >
          <Plus className="h-3.5 w-3.5" /> Thêm video sản phẩm
        </Button>
      }
    >
      <div className="flex flex-col gap-2">
        <Bookend
          kind="intro"
          item={broadcast.intro}
          current={broadcast.intro?.id === currentId}
          progress={progress}
          onPick={() => setPicker("intro")}
          onRemove={remove}
          onDuration={setDuration}
          playLabel={playLabel}
          onPlay={onPlay}
        />

        {items.length === 0 ? (
          <div className="rounded-xl border border-dashed border-studio-edge px-4 py-8 text-center text-sm text-studio-muted">
            Chưa có video sản phẩm. Bấm <strong>Thêm video sản phẩm</strong> để chọn các video đã
            tạo bằng Google Flow.
          </div>
        ) : (
          <ol className="flex flex-col gap-2" data-testid="Playlist.items">
            {items.map((item, index) => (
              <li
                key={item.id}
                data-testid="Playlist.row"
                draggable
                onDragStart={(e) => {
                  setDragId(item.id);
                  e.dataTransfer.effectAllowed = "move";
                  e.dataTransfer.setData("text/plain", item.id);
                }}
                onDragOver={(e) => {
                  if (!dragId || dragId === item.id) return;
                  e.preventDefault();
                  setOverId(item.id);
                }}
                onDragLeave={() => setOverId((x) => (x === item.id ? null : x))}
                onDrop={(e) => {
                  e.preventDefault();
                  if (dragId && dragId !== item.id) reorder(moveById(items, dragId, item.id));
                  setDragId(null);
                  setOverId(null);
                }}
                onDragEnd={() => {
                  setDragId(null);
                  setOverId(null);
                }}
                className={dragId === item.id ? "opacity-50" : ""}
              >
                <ItemRow
                  item={item}
                  number={index + 1}
                  current={item.id === currentId}
                  progress={progress}
                  highlight={overId === item.id}
                  playLabel={playLabel}
                  onPlay={onPlay}
                  onDuration={setDuration}
                  onRemove={remove}
                  moves={
                    <>
                      <Button
                        size="sm"
                        variant="quiet"
                        aria-label={`Đưa ${item.title} lên`}
                        disabled={index === 0 || busy}
                        onClick={() => reorder(moveItem(items, index, index - 1))}
                      >
                        <ArrowUp className="h-3.5 w-3.5" />
                      </Button>
                      <Button
                        size="sm"
                        variant="quiet"
                        aria-label={`Đưa ${item.title} xuống`}
                        disabled={index === items.length - 1 || busy}
                        onClick={() => reorder(moveItem(items, index, index + 1))}
                      >
                        <ArrowDown className="h-3.5 w-3.5" />
                      </Button>
                    </>
                  }
                />
              </li>
            ))}
          </ol>
        )}

        <Bookend
          kind="outro"
          item={broadcast.outro}
          current={broadcast.outro?.id === currentId}
          progress={progress}
          onPick={() => setPicker("outro")}
          onRemove={remove}
          onDuration={setDuration}
          playLabel={playLabel}
          onPlay={onPlay}
        />
      </div>

      <LoopSettings key={broadcast.loopCount} broadcast={broadcast} edit={edit} />

      {picker && (
        <VideoPicker role={picker} adding={busy} onAdd={add} onClose={() => setPicker(null)} />
      )}
    </Panel>
  );
}

function ItemRow({
  item,
  number,
  current,
  progress,
  highlight = false,
  playLabel,
  onPlay,
  onDuration,
  onRemove,
  moves,
}: {
  item: BroadcastItem;
  number: number | string;
  current: boolean;
  progress: number;
  highlight?: boolean;
  playLabel: string;
  onPlay: (itemId: string) => void;
  onDuration: (item: BroadcastItem, raw: string) => void;
  onRemove: (item: BroadcastItem) => void;
  moves?: ReactNode;
}) {
  const plays = playsInSlot(item);
  const price = item.product ? formatPrice(item.product.price, item.product.currency) : "";
  const meta = [price, item.hostName, item.videoS ? `video ${Math.round(item.videoS)}s` : ""]
    .filter(Boolean)
    .join(" · ");
  return (
    <div
      data-current={current || undefined}
      className={`relative overflow-hidden rounded-xl border bg-studio-card p-2 ${
        highlight
          ? "border-amber"
          : current
            ? "border-live-edge bg-live-tint"
            : "border-studio-border"
      }`}
    >
      <div className="flex flex-wrap items-center gap-2">
        {moves && (
          <span className="cursor-grab text-studio-faint active:cursor-grabbing" aria-hidden>
            <GripVertical className="h-4 w-4" />
          </span>
        )}
        <span className="w-6 shrink-0 text-center text-xs font-semibold text-studio-muted">
          {number}
        </span>
        <div className="h-14 w-8 shrink-0 overflow-hidden rounded-md bg-studio-hover">
          {item.thumbUrl && (
            <img
              src={item.thumbUrl}
              alt=""
              className="h-full w-full object-cover"
              draggable={false}
            />
          )}
        </div>
        <div className="min-w-[9rem] flex-1">
          <p className="truncate text-sm font-medium">
            {current && <span className="mr-1.5 text-live">●</span>}
            {item.title}
          </p>
          <p className="truncate text-xs text-studio-muted">{meta || "Video tải lên"}</p>
          {item.problem && <Badge tone="danger">{item.problem}</Badge>}
          {!item.problem && plays !== null && plays > 1.05 && (
            <p className="text-[11px] text-studio-faint">
              <Repeat className="mr-1 inline h-3 w-3" />
              lặp {String(plays).replace(".", ",")} lần trong {formatSpan(item.slotS ?? 0)}
            </p>
          )}
        </div>
        <div className="ml-auto flex shrink-0 items-center gap-1">
          <select
            aria-label={`Thời lượng phát của ${item.title}`}
            data-testid="Playlist.duration"
            className={`${compactInput} w-40 shrink-0 py-1.5 text-xs`}
            value={item.durationS ?? ""}
            onChange={(e) => onDuration(item, e.target.value)}
          >
            {durationChoices(item.videoS, item.durationS).map((choice) => (
              <option key={choice.value ?? "once"} value={choice.value ?? ""}>
                {choice.label}
              </option>
            ))}
            <option value="custom">Tùy chỉnh...</option>
          </select>
          <div className="flex shrink-0 items-center">
            <Button
              size="sm"
              variant="quiet"
              aria-label={`${playLabel}: ${item.title}`}
              title={playLabel}
              disabled={!item.playable}
              onClick={() => onPlay(item.id)}
              data-testid="Playlist.play"
            >
              <Play className="h-3.5 w-3.5" />
            </Button>
            {moves}
            <Button
              size="sm"
              variant="quiet"
              aria-label={`Gỡ ${item.title} khỏi phiên`}
              onClick={() => onRemove(item)}
              data-testid="Playlist.remove"
            >
              <Trash2 className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>
      </div>
      {current && (
        <div className="absolute inset-x-0 bottom-0 h-0.5 bg-studio-hover">
          <div
            className="h-full bg-live transition-[width] duration-300"
            style={{ width: `${Math.min(progress, 1) * 100}%` }}
          />
        </div>
      )}
    </div>
  );
}

function Bookend({
  kind: role,
  item,
  current,
  progress,
  onPick,
  onRemove,
  onDuration,
  playLabel,
  onPlay,
}: {
  kind: "intro" | "outro";
  item: BroadcastItem | null;
  current: boolean;
  progress: number;
  onPick: () => void;
  onRemove: (item: BroadcastItem) => void;
  onDuration: (item: BroadcastItem, raw: string) => void;
  playLabel: string;
  onPlay: (itemId: string) => void;
}) {
  const name = role === "intro" ? "Mở đầu" : "Kết thúc";
  if (!item) {
    return (
      <button
        type="button"
        onClick={onPick}
        data-testid={`Playlist.${role}`}
        className="flex items-center gap-2 rounded-xl border border-dashed border-studio-border px-3 py-2 text-left text-xs text-studio-muted hover:bg-studio-hover hover:text-studio-fg"
      >
        <Plus className="h-3.5 w-3.5" />
        Thêm video {name.toLowerCase()} (không bắt buộc,{" "}
        {role === "intro" ? "phát một lần khi bắt đầu" : "phát một lần trước khi dừng"})
      </button>
    );
  }
  return (
    <div className="flex items-center gap-2">
      <div className="min-w-0 flex-1">
        <ItemRow
          item={item}
          number={name}
          current={current}
          progress={progress}
          playLabel={playLabel}
          onPlay={onPlay}
          onDuration={onDuration}
          onRemove={onRemove}
        />
      </div>
      <Button size="sm" onClick={onPick} className="shrink-0">
        Đổi
      </Button>
    </div>
  );
}

function LoopSettings({ broadcast, edit }: { broadcast: Broadcast; edit: Edit }) {
  const [count, setCount] = useState(String(broadcast.loopCount));
  const save = (loopMode: "continuous" | "count", loopCount?: number) =>
    void edit("Không lưu được chế độ lặp", () =>
      updateBroadcast(broadcast.id, { loopMode, loopCount }),
    );
  const commitCount = () => {
    const n = Math.round(Number(count));
    if (!Number.isFinite(n) || n < 1 || n > 1000) {
      setCount(String(broadcast.loopCount));
      return;
    }
    if (n !== broadcast.loopCount || broadcast.loopMode !== "count") save("count", n);
  };
  return (
    <fieldset className="mt-4 flex flex-wrap items-center gap-x-5 gap-y-2 border-t border-studio-border pt-3 text-sm">
      <legend className="sr-only">Chế độ lặp</legend>
      <label className="inline-flex cursor-pointer items-center gap-2">
        <input
          type="radio"
          name="loop-mode"
          checked={broadcast.loopMode === "continuous"}
          onChange={() => save("continuous")}
          className="accent-amber"
        />
        <InfinityIcon className="h-4 w-4 text-studio-muted" /> Phát liên tục đến khi dừng
      </label>
      <label className="inline-flex cursor-pointer items-center gap-2">
        <input
          type="radio"
          name="loop-mode"
          checked={broadcast.loopMode === "count"}
          onChange={() => save("count", Number(count) || 1)}
          className="accent-amber"
        />
        Lặp
        <input
          type="number"
          min={1}
          max={1000}
          value={count}
          aria-label="Số vòng lặp"
          onChange={(e) => setCount(e.target.value)}
          onBlur={commitCount}
          onKeyDown={(e) => e.key === "Enter" && commitCount()}
          className={`${compactInput} w-20 py-1`}
        />
        vòng rồi kết thúc
      </label>
    </fieldset>
  );
}
