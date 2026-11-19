"use client";

// Step 1: the product catalog. Pick the product to make a video for.

import { Plus, Search } from "lucide-react";
import { useMemo, useState } from "react";
import { apiBaseUrl } from "@/lib/api";
import type { CatalogProduct } from "@/lib/promo/types";
import { formatPrice, resolveMediaUrl } from "@/lib/studio/logic";
import { Badge, Button, ErrorNote, inputClass, Panel, Spinner } from "@/components/studio/ui";

export function CatalogPanel({
  products,
  loading,
  error,
  selectedId,
  onSelect,
  onCreate,
}: {
  products: CatalogProduct[];
  loading: boolean;
  error: string | null;
  selectedId: string | null;
  onSelect: (id: string) => void;
  onCreate: () => void;
}) {
  const [query, setQuery] = useState("");
  const shown = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return products;
    return products.filter((p) =>
      [p.name, p.brand, p.category, p.sku].some((v) => v.toLowerCase().includes(q)),
    );
  }, [products, query]);

  return (
    <Panel
      title={`1. Sản phẩm (${products.length})`}
      action={
        <Button variant="primary" size="sm" onClick={onCreate} data-testid="Catalog.new">
          <Plus className="h-3.5 w-3.5" /> Thêm
        </Button>
      }
      className="flex min-h-0 flex-col"
    >
      <div className="relative mb-3">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 h-4 w-4 text-studio-faint" />
        <input
          className={`${inputClass} pl-8`}
          placeholder="Tìm theo tên, thương hiệu, mã"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          aria-label="Tìm sản phẩm"
        />
      </div>
      <ErrorNote message={error} />
      {loading && (
        <p className="flex items-center gap-2 text-sm text-studio-muted">
          <Spinner /> Đang tải danh mục...
        </p>
      )}
      <ul className="flex flex-col gap-2 overflow-y-auto pr-1 lg:max-h-[calc(100dvh-14rem)]">
        {shown.map((p) => {
          const image = resolveMediaUrl(p.imageUrl, apiBaseUrl());
          const active = p.id === selectedId;
          return (
            <li key={p.id}>
              <button
                type="button"
                onClick={() => onSelect(p.id)}
                data-testid="Catalog.item"
                aria-pressed={active}
                className={`flex w-full gap-3 rounded-xl border p-2 text-left transition ${
                  active
                    ? "border-amber bg-amber-tint"
                    : "border-studio-border bg-studio-card hover:bg-studio-hover"
                }`}
              >
                <div className="h-16 w-16 shrink-0 overflow-hidden rounded-lg bg-studio-hover">
                  {image && <img src={image} alt="" className="h-full w-full object-cover" />}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-semibold text-studio-fg">{p.name}</p>
                  <p className="truncate text-xs text-studio-muted">
                    {[p.brand, p.category].filter(Boolean).join(" · ") || p.sku}
                  </p>
                  <div className="mt-1 flex flex-wrap items-center gap-1.5">
                    {p.price !== null && (
                      <span className="text-xs font-semibold text-amber">
                        {formatPrice(p.price, p.currency)}
                      </span>
                    )}
                    {p.imageIsPlaceholder && <Badge tone="danger">Chưa có ảnh thật</Badge>}
                  </div>
                </div>
              </button>
            </li>
          );
        })}
        {!loading && shown.length === 0 && (
          <li className="py-6 text-center text-sm text-studio-muted">Không có sản phẩm phù hợp.</li>
        )}
      </ul>
    </Panel>
  );
}
