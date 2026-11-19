"use client";

// "Video sản phẩm AI": catalog -> pick a product -> AI script -> Google Flow
// renders it in the user's Chrome -> watch the finished video.

import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "next/navigation";
import { useEffect, useState } from "react";
import { listCatalog } from "@/lib/promo/api";
import { promoKeys } from "@/lib/promo/queryKeys";
import type { CatalogProduct } from "@/lib/promo/types";
import { describeError } from "@/lib/studio/api";
import { StudioNav } from "@/components/studio/StudioNav";
import { Panel } from "@/components/studio/ui";
import { CatalogPanel } from "./CatalogPanel";
import { FlowStatusBar } from "./FlowStatusBar";
import { ProductForm } from "./ProductForm";
import { ProductWorkspace } from "./ProductWorkspace";
import { VideoDetail } from "./VideoDetail";

type Editing = { mode: "create" } | { mode: "edit"; product: CatalogProduct } | null;

export function PromoStudio() {
  const catalog = useQuery({ queryKey: promoKeys.catalog, queryFn: listCatalog });
  const products = catalog.data ?? [];
  // `?product=<id>` (from the product list or the session video picker).
  const wanted = useSearchParams()?.get("product") ?? null;
  const [selectedId, setSelectedId] = useState<string | null>(wanted);
  const [videoId, setVideoId] = useState<string | null>(null);
  const [editing, setEditing] = useState<Editing>(null);

  // Default to the first product that has a real photo.
  useEffect(() => {
    if (!selectedId && products.length) {
      setSelectedId((products.find((p) => !p.imageIsPlaceholder) ?? products[0]).id);
    }
  }, [products, selectedId]);

  const selected = products.find((p) => p.id === selectedId) ?? null;

  return (
    <div className="min-h-[100dvh] bg-studio-bg text-studio-fg">
      <StudioNav />
      <div className="mx-auto flex max-w-7xl flex-col gap-4 px-4 py-6">
        <header className="flex flex-wrap items-end justify-between gap-3">
          <div>
            <h1 className="text-xl font-bold">Video giới thiệu sản phẩm bằng Google Flow</h1>
            <p className="mt-1 text-sm text-studio-muted">
              Chọn sản phẩm, AI viết kịch bản, Flow (Veo, Omni) dựng người mẫu AI cầm và dùng sản
              phẩm rồi nói lời thoại.
            </p>
          </div>
        </header>
        <FlowStatusBar />
        <div className="grid gap-4 lg:grid-cols-[22rem_1fr]">
          <CatalogPanel
            products={products}
            loading={catalog.isLoading}
            error={catalog.isError ? describeError(catalog.error) : null}
            selectedId={selectedId}
            onSelect={(id) => {
              setSelectedId(id);
              setVideoId(null);
              setEditing(null);
            }}
            onCreate={() => setEditing({ mode: "create" })}
          />
          <div className="flex min-w-0 flex-col gap-4">
            {editing ? (
              <Panel title={editing.mode === "create" ? "Thêm sản phẩm" : "Sửa sản phẩm"}>
                <ProductForm
                  key={editing.mode === "edit" ? editing.product.id : "new"}
                  product={editing.mode === "edit" ? editing.product : null}
                  onCancel={() => setEditing(null)}
                  onDone={(p) => {
                    setEditing(null);
                    setSelectedId(p.id);
                  }}
                />
              </Panel>
            ) : selected ? (
              <>
                <ProductWorkspace
                  product={selected}
                  selectedVideoId={videoId}
                  onSelectVideo={setVideoId}
                  onEdit={() => setEditing({ mode: "edit", product: selected })}
                />
                {videoId && (
                  <VideoDetail key={videoId} videoId={videoId} onDeleted={() => setVideoId(null)} />
                )}
              </>
            ) : (
              <Panel>
                <p className="text-sm text-studio-muted">
                  Chưa có sản phẩm. Bấm Thêm để tạo sản phẩm đầu tiên.
                </p>
              </Panel>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
