"use client";

// Step 1: the product catalog. Add, edit and remove products and see which
// ones already have AI videos, then jump to making one.

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Clapperboard, Pencil, Plus, Search, Trash2, X } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { deleteCatalogProduct, listCatalog, listVideos } from "@/lib/promo/api";
import { promoKeys } from "@/lib/promo/queryKeys";
import type { CatalogProduct, PromoVideo } from "@/lib/promo/types";
import { describeError } from "@/lib/studio/api";
import { formatPrice, resolveMediaUrl } from "@/lib/studio/logic";
import { apiBaseUrl } from "@/lib/api";
import { useToast } from "@/lib/ui/toast";
import { ProductForm } from "@/components/promo/ProductForm";
import { StudioNav } from "@/components/studio/StudioNav";
import { Badge, Button, ErrorNote, inputClass } from "@/components/studio/ui";

type Filter = "all" | "no-photo" | "no-video";
type Editing = { product: CatalogProduct | null } | null;

const FILTERS: [Filter, string][] = [
  ["all", "Tất cả"],
  ["no-video", "Chưa có video"],
  ["no-photo", "Chưa có ảnh thật"],
];

export function ProductsPage() {
  const qc = useQueryClient();
  const toast = useToast();
  const catalog = useQuery({ queryKey: promoKeys.catalog, queryFn: listCatalog });
  const videos = useQuery({ queryKey: ["promo", "videos", "all"], queryFn: () => listVideos() });
  const [query, setQuery] = useState("");
  const [filter, setFilter] = useState<Filter>("all");
  const [editing, setEditing] = useState<Editing>(null);

  const videosByProduct = useMemo(() => {
    const map = new Map<string, PromoVideo[]>();
    for (const v of videos.data ?? []) map.set(v.productId, [...(map.get(v.productId) ?? []), v]);
    return map;
  }, [videos.data]);

  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    return (catalog.data ?? []).filter((p) => {
      if (q && ![p.name, p.brand, p.category, p.sku].some((v) => v.toLowerCase().includes(q))) {
        return false;
      }
      const ready = (videosByProduct.get(p.id) ?? []).some((v) => v.status === "ready");
      if (filter === "no-video") return !ready;
      if (filter === "no-photo") return p.imageIsPlaceholder;
      return true;
    });
  }, [catalog.data, query, filter, videosByProduct]);

  const remove = useMutation({
    mutationFn: deleteCatalogProduct,
    onSuccess: () => qc.invalidateQueries({ queryKey: promoKeys.catalog }),
    onError: (err) =>
      toast.show({ kind: "error", title: "Không xóa được sản phẩm", body: describeError(err) }),
  });

  return (
    <div className="min-h-[100dvh] bg-studio-bg text-studio-fg">
      <StudioNav />
      <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-6">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold">Sản phẩm</h1>
            <p className="mt-1 text-sm text-studio-muted">
              Thông tin ở đây là nguồn duy nhất AI dùng để viết kịch bản video: tên, ảnh thật, mô
              tả, đặc điểm nổi bật, giá và nội dung cần giới thiệu.
            </p>
          </div>
          <Button
            variant="primary"
            onClick={() => setEditing({ product: null })}
            data-testid="Products.new"
          >
            <Plus className="h-4 w-4" /> Thêm sản phẩm
          </Button>
        </header>

        <div className="flex flex-wrap items-center gap-2">
          <div className="relative min-w-[14rem] flex-1">
            <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-studio-faint" />
            <input
              className={`${inputClass} pl-8`}
              placeholder="Tìm theo tên, thương hiệu, danh mục, mã"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              aria-label="Tìm sản phẩm"
            />
          </div>
          {FILTERS.map(([key, label]) => (
            <button
              key={key}
              type="button"
              aria-pressed={filter === key}
              onClick={() => setFilter(key)}
              className={`rounded-full border px-3 py-1.5 text-xs ${
                filter === key
                  ? "border-amber bg-amber-tint text-amber"
                  : "border-studio-border text-studio-muted hover:text-studio-fg"
              }`}
            >
              {label}
            </button>
          ))}
        </div>

        <ErrorNote message={catalog.isError ? describeError(catalog.error) : null} />
        {catalog.isLoading && <p className="text-sm text-studio-muted">Đang tải sản phẩm...</p>}
        {catalog.data && shown.length === 0 && (
          <p className="py-10 text-center text-sm text-studio-muted">Không có sản phẩm phù hợp.</p>
        )}

        <ul className="grid gap-3 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4">
          {shown.map((product) => (
            <ProductCard
              key={product.id}
              product={product}
              videos={videosByProduct.get(product.id) ?? []}
              deleting={remove.isPending && remove.variables === product.id}
              onEdit={() => setEditing({ product })}
              onDelete={() => {
                if (
                  window.confirm(
                    `Xóa sản phẩm "${product.name}"? Các video đã tạo vẫn được giữ lại.`,
                  )
                ) {
                  remove.mutate(product.id);
                }
              }}
            />
          ))}
        </ul>
      </div>

      {editing && <EditorDialog product={editing.product} onClose={() => setEditing(null)} />}
    </div>
  );
}

function ProductCard({
  product,
  videos,
  deleting,
  onEdit,
  onDelete,
}: {
  product: CatalogProduct;
  videos: PromoVideo[];
  deleting: boolean;
  onEdit: () => void;
  onDelete: () => void;
}) {
  const image = resolveMediaUrl(product.imageUrl, apiBaseUrl());
  const ready = videos.filter((v) => v.status === "ready").length;
  return (
    <li
      data-testid="Products.card"
      className="flex flex-col overflow-hidden rounded-2xl border border-studio-border bg-studio-panel"
    >
      <div className="relative aspect-[4/3] overflow-hidden bg-studio-card">
        {image && (
          <img
            src={image}
            alt={product.name}
            className="absolute inset-0 h-full w-full object-contain"
          />
        )}
        {product.imageIsPlaceholder && (
          <span className="absolute left-2 top-2">
            <Badge tone="danger">Chưa có ảnh thật</Badge>
          </span>
        )}
      </div>
      <div className="flex flex-1 flex-col gap-1 p-3">
        <p className="line-clamp-2 text-sm font-semibold">{product.name}</p>
        <p className="truncate text-xs text-studio-muted">
          {[product.brand, product.category].filter(Boolean).join(" · ") || product.sku}
        </p>
        {product.price !== null && (
          <p className="text-sm font-semibold text-amber">
            {formatPrice(product.price, product.currency)}
          </p>
        )}
        <p className="text-xs text-studio-muted">
          {product.highlights.length} đặc điểm nổi bật
          {product.talkingPoints ? " · có nội dung giới thiệu" : ""}
        </p>
        <div className="mt-1">
          {ready > 0 ? (
            <Badge tone="live">{ready} video sẵn sàng</Badge>
          ) : videos.length > 0 ? (
            <Badge tone="amber">{videos.length} video đang làm</Badge>
          ) : (
            <Badge>Chưa có video</Badge>
          )}
        </div>
        <div className="mt-auto flex items-center gap-1 pt-3">
          <Link
            href={`/studio/videos?product=${encodeURIComponent(product.id)}`}
            className="inline-flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-amber px-2.5 py-1.5 text-xs font-semibold text-amber-fg hover:bg-amber-hover"
          >
            <Clapperboard className="h-3.5 w-3.5" /> Tạo video
          </Link>
          <Button size="sm" onClick={onEdit} aria-label={`Sửa ${product.name}`}>
            <Pencil className="h-3.5 w-3.5" />
          </Button>
          <Button
            size="sm"
            variant="quiet"
            onClick={onDelete}
            busy={deleting}
            aria-label={`Xóa ${product.name}`}
          >
            <Trash2 className="h-3.5 w-3.5" />
          </Button>
        </div>
      </div>
    </li>
  );
}

function EditorDialog({
  product,
  onClose,
}: {
  product: CatalogProduct | null;
  onClose: () => void;
}) {
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);
  return (
    <div
      className="fixed inset-0 z-50 flex items-end justify-center bg-black/70 sm:items-center sm:p-4"
      onMouseDown={(e) => e.target === e.currentTarget && onClose()}
      role="presentation"
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-label={product ? "Sửa sản phẩm" : "Thêm sản phẩm"}
        className="max-h-[92dvh] w-full max-w-2xl overflow-y-auto rounded-t-2xl border border-studio-border bg-studio-panel p-4 sm:rounded-2xl"
      >
        <div className="mb-3 flex items-center justify-between">
          <h2 className="text-sm font-semibold">{product ? "Sửa sản phẩm" : "Thêm sản phẩm"}</h2>
          <Button size="sm" variant="quiet" onClick={onClose} aria-label="Đóng">
            <X className="h-4 w-4" />
          </Button>
        </div>
        <ProductForm product={product} onCancel={onClose} onDone={onClose} />
      </div>
    </div>
  );
}
