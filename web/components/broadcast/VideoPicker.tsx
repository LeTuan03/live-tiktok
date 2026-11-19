"use client";

// Pick the videos to add to a session: finished Google Flow promo videos,
// grouped by product with a version number each, or a video file uploaded
// from the computer (an intro or outro made elsewhere, for instance).

import { useQuery } from "@tanstack/react-query";
import { Check, Search, Upload, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { uploadVideo } from "@/lib/broadcast/api";
import { formatSpan, versionNumbers } from "@/lib/broadcast/logic";
import type { ItemRole, NewItem, UploadedVideo } from "@/lib/broadcast/types";
import { listCatalog, listVideos, videoUrls } from "@/lib/promo/api";
import { STATUS_LABEL as VIDEO_STATUS } from "@/lib/promo/logic";
import { promoKeys } from "@/lib/promo/queryKeys";
import type { CatalogProduct, PromoVideo } from "@/lib/promo/types";
import { describeError } from "@/lib/studio/api";
import { formatPrice } from "@/lib/studio/logic";
import { Badge, Button, ErrorNote, inputClass } from "@/components/studio/ui";

const ROLE_TITLE: Record<ItemRole, string> = {
  product: "Thêm video sản phẩm vào phiên",
  intro: "Chọn video mở đầu",
  outro: "Chọn video kết thúc",
};

interface Group {
  productId: string;
  name: string;
  product: CatalogProduct | null;
  videos: PromoVideo[];
}

export function VideoPicker({
  role,
  adding,
  onAdd,
  onClose,
}: {
  role: ItemRole;
  adding: boolean;
  onAdd: (items: NewItem[]) => void;
  onClose: () => void;
}) {
  const multi = role === "product";
  const [tab, setTab] = useState<"flow" | "upload">("flow");
  const [selected, setSelected] = useState<string[]>([]);
  const [query, setQuery] = useState("");

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const videos = useQuery({ queryKey: ["promo", "videos", "all"], queryFn: () => listVideos() });
  const catalog = useQuery({ queryKey: promoKeys.catalog, queryFn: listCatalog });
  const versions = useMemo(() => versionNumbers(videos.data ?? []), [videos.data]);

  const groups = useMemo(() => {
    const byId = new Map<string, Group>();
    for (const product of catalog.data ?? []) {
      byId.set(product.id, { productId: product.id, name: product.name, product, videos: [] });
    }
    for (const video of videos.data ?? []) {
      const group = byId.get(video.productId) ?? {
        productId: video.productId,
        name: video.productName,
        product: null,
        videos: [],
      };
      group.videos.push(video);
      byId.set(video.productId, group);
    }
    const q = query.trim().toLowerCase();
    return [...byId.values()]
      .filter((g) => !q || g.name.toLowerCase().includes(q))
      .sort((a, b) => b.videos.length - a.videos.length || a.name.localeCompare(b.name));
  }, [catalog.data, videos.data, query]);

  const toggle = (id: string) =>
    setSelected((prev) =>
      prev.includes(id) ? prev.filter((x) => x !== id) : multi ? [...prev, id] : [id],
    );

  const latestOfEach = () => {
    const picks = groups
      .map((g) => g.videos.find((v) => v.status === "ready"))
      .filter((v): v is PromoVideo => !!v)
      .map((v) => v.id);
    setSelected(picks);
  };

  const addSelected = () =>
    onAdd(selected.map((id) => ({ role, source: "promo" as const, sourceRef: id })));

  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 p-0 sm:items-center sm:p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={ROLE_TITLE[role]}
        data-testid="VideoPicker"
        className="flex max-h-[92dvh] w-full max-w-3xl flex-col overflow-hidden rounded-t-2xl border border-studio-border bg-studio-panel sm:rounded-2xl"
      >
        <header className="flex items-center justify-between gap-3 border-b border-studio-border px-4 py-3">
          <h2 className="text-sm font-semibold">{ROLE_TITLE[role]}</h2>
          <Button size="sm" variant="quiet" onClick={onClose} aria-label="Đóng">
            <X className="h-4 w-4" />
          </Button>
        </header>
        <div className="flex gap-1 border-b border-studio-border px-4 pt-2">
          {(
            [
              ["flow", "Video AI đã tạo"],
              ["upload", "Tải video lên"],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              type="button"
              onClick={() => setTab(key)}
              className={`rounded-t-lg px-3 py-2 text-sm ${
                tab === key
                  ? "border-b-2 border-amber font-semibold text-studio-fg"
                  : "text-studio-muted hover:text-studio-fg"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === "flow" ? (
          <>
            <div className="flex flex-wrap items-center gap-2 px-4 py-3">
              <div className="relative min-w-[12rem] flex-1">
                <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-studio-faint" />
                <input
                  className={`${inputClass} pl-8`}
                  placeholder="Tìm sản phẩm"
                  value={query}
                  onChange={(e) => setQuery(e.target.value)}
                  aria-label="Tìm sản phẩm"
                />
              </div>
              {multi && (
                <Button size="sm" onClick={latestOfEach} data-testid="VideoPicker.latest">
                  Chọn bản mới nhất của mỗi sản phẩm
                </Button>
              )}
            </div>
            <div className="min-h-0 flex-1 overflow-y-auto px-4 pb-4">
              <ErrorNote message={videos.isError ? describeError(videos.error) : null} />
              {videos.isLoading && <p className="text-sm text-studio-faint">Đang tải video...</p>}
              <div className="flex flex-col gap-4">
                {groups.map((group) => (
                  <ProductGroup
                    key={group.productId}
                    group={group}
                    versions={versions}
                    selected={selected}
                    onToggle={toggle}
                  />
                ))}
              </div>
            </div>
            <footer className="flex items-center justify-between gap-3 border-t border-studio-border px-4 py-3">
              <p className="text-xs text-studio-muted">
                {selected.length ? `Đã chọn ${selected.length} video` : "Chọn video đã hoàn tất"}
              </p>
              <div className="flex gap-2">
                <Button variant="quiet" onClick={onClose}>
                  Hủy
                </Button>
                <Button
                  variant="primary"
                  disabled={selected.length === 0}
                  busy={adding}
                  onClick={addSelected}
                  data-testid="VideoPicker.add"
                >
                  {multi ? "Thêm vào phiên" : "Dùng video này"}
                </Button>
              </div>
            </footer>
          </>
        ) : (
          <UploadTab
            role={role}
            products={catalog.data ?? []}
            adding={adding}
            onAdd={onAdd}
            onCancel={onClose}
          />
        )}
      </div>
    </div>
  );
}

function ProductGroup({
  group,
  versions,
  selected,
  onToggle,
}: {
  group: Group;
  versions: Map<string, number>;
  selected: string[];
  onToggle: (id: string) => void;
}) {
  const price = group.product ? formatPrice(group.product.price, group.product.currency) : "";
  return (
    <section>
      <div className="mb-2 flex items-baseline justify-between gap-2">
        <h3 className="truncate text-sm font-semibold">
          {group.name}
          {price && <span className="ml-2 text-xs font-normal text-amber">{price}</span>}
        </h3>
        <Link
          href={`/studio/videos?product=${encodeURIComponent(group.productId)}`}
          className="shrink-0 text-xs text-studio-muted hover:text-studio-fg hover:underline"
        >
          {group.videos.length ? "Tạo thêm video" : "Chưa có video, tạo video"}
        </Link>
      </div>
      {group.videos.length > 0 && (
        <ul className="grid grid-cols-3 gap-2 sm:grid-cols-5">
          {group.videos.map((video) => {
            const ready = video.status === "ready" && video.hasVideo;
            const picked = selected.includes(video.id);
            return (
              <li key={video.id}>
                <button
                  type="button"
                  disabled={!ready}
                  onClick={() => onToggle(video.id)}
                  aria-pressed={picked}
                  data-testid="VideoPicker.video"
                  className={`relative w-full overflow-hidden rounded-xl border text-left transition disabled:cursor-not-allowed disabled:opacity-50 ${
                    picked ? "border-amber ring-2 ring-amber" : "border-studio-border"
                  } bg-studio-card`}
                >
                  <div className="aspect-[9/16] w-full bg-studio-hover">
                    {video.hasThumb && (
                      <img
                        src={videoUrls(video).thumb}
                        alt=""
                        className="h-full w-full object-cover"
                      />
                    )}
                  </div>
                  {picked && (
                    <span className="absolute right-1.5 top-1.5 rounded-full bg-amber p-0.5 text-amber-fg">
                      <Check className="h-3.5 w-3.5" />
                    </span>
                  )}
                  <div className="p-1.5 text-[11px] leading-tight">
                    <p className="font-semibold">
                      Bản {versions.get(video.id) ?? 1}
                      {video.durationS ? ` · ${Math.round(video.durationS)}s` : ""}
                    </p>
                    <p className="truncate text-studio-muted">{video.hostName}</p>
                    {!ready && <Badge tone="amber">{VIDEO_STATUS[video.status]}</Badge>}
                  </div>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

function UploadTab({
  role,
  products,
  adding,
  onAdd,
  onCancel,
}: {
  role: ItemRole;
  products: CatalogProduct[];
  adding: boolean;
  onAdd: (items: NewItem[]) => void;
  onCancel: () => void;
}) {
  const [progress, setProgress] = useState<number | null>(null);
  const [uploaded, setUploaded] = useState<UploadedVideo | null>(null);
  const [productId, setProductId] = useState("");
  const [error, setError] = useState<string | null>(null);

  const onFile = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    setUploaded(null);
    setProgress(0);
    try {
      setUploaded(await uploadVideo(file, setProgress));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setProgress(null);
    }
  };

  const add = () => {
    if (!uploaded) return;
    const stem = uploaded.name.replace(/\.[^.]+$/, "");
    onAdd([
      {
        role,
        source: "upload",
        sourceRef: uploaded.url,
        productId: productId || null,
        label: role === "product" && !productId ? stem : "",
      },
    ]);
  };

  return (
    <div className="flex flex-col gap-3 p-4">
      <label className="flex cursor-pointer flex-col items-center justify-center gap-2 rounded-xl border border-dashed border-studio-edge bg-studio-card px-4 py-8 text-center text-sm text-studio-muted hover:bg-studio-hover">
        <Upload className="h-6 w-6" />
        {progress !== null
          ? `Đang tải lên ${Math.round(progress * 100)}%...`
          : "Chọn tệp video MP4 (H.264) hoặc WebM, tối đa 500 MB"}
        <input
          type="file"
          accept="video/mp4,video/webm,video/quicktime"
          className="sr-only"
          disabled={progress !== null}
          onChange={(e) => onFile(e.target.files?.[0])}
          data-testid="VideoPicker.file"
        />
      </label>
      {progress !== null && (
        <div className="h-1.5 overflow-hidden rounded-full bg-studio-hover">
          <div className="h-full bg-amber" style={{ width: `${Math.round(progress * 100)}%` }} />
        </div>
      )}
      <ErrorNote message={error} />
      {uploaded && (
        <div className="flex gap-3 rounded-xl border border-studio-border bg-studio-card p-3">
          <div className="h-24 w-14 shrink-0 overflow-hidden rounded-md bg-black">
            {uploaded.thumbUrl && (
              <img src={uploaded.thumbUrl} alt="" className="h-full w-full object-cover" />
            )}
          </div>
          <div className="min-w-0 flex-1 text-sm">
            <p className="truncate font-semibold">{uploaded.name}</p>
            <p className="text-xs text-studio-muted">
              {formatSpan(uploaded.durationS)} · {uploaded.width}x{uploaded.height}
            </p>
            {uploaded.width > uploaded.height && (
              <p className="mt-1 text-xs text-amber">
                Video ngang sẽ có viền đen khi phát trên khung dọc 9:16.
              </p>
            )}
            {role === "product" && (
              <select
                className={`${inputClass} mt-2`}
                value={productId}
                onChange={(e) => setProductId(e.target.value)}
                aria-label="Sản phẩm trong video"
              >
                <option value="">Không gắn sản phẩm</option>
                {products.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name}
                  </option>
                ))}
              </select>
            )}
          </div>
        </div>
      )}
      <div className="flex justify-end gap-2">
        <Button variant="quiet" onClick={onCancel}>
          Hủy
        </Button>
        <Button variant="primary" disabled={!uploaded} busy={adding} onClick={add}>
          {role === "product" ? "Thêm vào phiên" : "Dùng video này"}
        </Button>
      </div>
    </div>
  );
}
