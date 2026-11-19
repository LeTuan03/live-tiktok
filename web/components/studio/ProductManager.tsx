"use client";

// Ordered product list for one livestream session: add, edit, delete,
// reorder (drag and drop plus up/down buttons) and, while live, jump the
// model to any product.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ArrowDown, ArrowUp, GripVertical, Pencil, Play, Plus, Trash2 } from "lucide-react";
import { useState } from "react";
import { apiBaseUrl } from "@/lib/api";
import { deleteProduct, describeError, reorderProducts } from "@/lib/studio/api";
import {
  formatPrice,
  moveById,
  moveItem,
  PRODUCT_STATUS_LABEL,
  resolveMediaUrl,
  sameOrder,
} from "@/lib/studio/logic";
import { studioKeys } from "@/lib/studio/queryKeys";
import type { ProductStatus, StudioProduct, StudioSession } from "@/lib/studio/types";
import { useToast } from "@/lib/ui/toast";
import { ProductEditor } from "./ProductEditor";
import { Badge, Button, Panel } from "./ui";

const STATUS_TONE: Record<ProductStatus, "neutral" | "live" | "amber"> = {
  pending: "neutral",
  presenting: "live",
  done: "amber",
  skipped: "neutral",
};

export interface ProductManagerProps {
  session: StudioSession;
  /** True when the "Giới thiệu ngay" control applies (session is live). */
  canPresent: boolean;
  /** Id of the product whose present request is in flight, if any. */
  presentingRequestId: string | null;
  currentProductId: string | null;
  onPresent: (productId: string) => void;
}

export function ProductManager({
  session,
  canPresent,
  presentingRequestId,
  currentProductId,
  onPresent,
}: ProductManagerProps) {
  const qc = useQueryClient();
  const toast = useToast();
  const key = studioKeys.session(session.id);
  const products = session.products;

  const [editor, setEditor] = useState<{ product: StudioProduct | null } | null>(null);
  const [dragId, setDragId] = useState<string | null>(null);
  const [overId, setOverId] = useState<string | null>(null);

  const upsert = (product: StudioProduct) => {
    qc.setQueryData<StudioSession>(key, (prev) => {
      if (!prev) return prev;
      const exists = prev.products.some((p) => p.id === product.id);
      const next = exists
        ? prev.products.map((p) => (p.id === product.id ? product : p))
        : [...prev.products, product];
      return { ...prev, products: [...next].sort((a, b) => a.position - b.position) };
    });
    void qc.invalidateQueries({ queryKey: studioKeys.sessions });
  };

  const reorder = useMutation({
    mutationFn: (ids: string[]) => reorderProducts(session.id, ids),
    onMutate: async (ids) => {
      await qc.cancelQueries({ queryKey: key });
      const previous = qc.getQueryData<StudioSession>(key);
      qc.setQueryData<StudioSession>(key, (prev) => {
        if (!prev) return prev;
        const byId = new Map(prev.products.map((p) => [p.id, p]));
        const ordered = ids
          .map((id) => byId.get(id))
          .filter((p): p is StudioProduct => p !== undefined)
          .map((p, index) => ({ ...p, position: index }));
        return { ...prev, products: ordered };
      });
      return { previous };
    },
    onError: (err, _ids, context) => {
      if (context?.previous) qc.setQueryData(key, context.previous);
      toast.show({ kind: "error", title: "Không đổi được thứ tự", body: describeError(err) });
    },
    // Reconcile with what the server stored, not what we guessed.
    onSuccess: (serverProducts) => {
      qc.setQueryData<StudioSession>(key, (prev) =>
        prev ? { ...prev, products: serverProducts } : prev,
      );
    },
  });

  const remove = useMutation({
    mutationFn: deleteProduct,
    onSuccess: (_data, productId) => {
      qc.setQueryData<StudioSession>(key, (prev) =>
        prev ? { ...prev, products: prev.products.filter((p) => p.id !== productId) } : prev,
      );
      void qc.invalidateQueries({ queryKey: key });
      void qc.invalidateQueries({ queryKey: studioKeys.sessions });
    },
    onError: (err) =>
      toast.show({ kind: "error", title: "Không xóa được sản phẩm", body: describeError(err) }),
  });

  const applyOrder = (next: StudioProduct[]) => {
    if (sameOrder(next, products)) return;
    reorder.mutate(next.map((p) => p.id));
  };

  return (
    <Panel
      title={`Sản phẩm (${products.length})`}
      action={
        <Button
          variant="primary"
          size="sm"
          data-testid="Products.add"
          onClick={() => setEditor({ product: null })}
        >
          <Plus className="h-3.5 w-3.5" /> Thêm sản phẩm
        </Button>
      }
    >
      {products.length === 0 ? (
        <p className="text-sm text-studio-faint">
          Chưa có sản phẩm. Thêm sản phẩm để người mẫu giới thiệu theo thứ tự trong danh sách.
        </p>
      ) : (
        <ol className="flex flex-col gap-2" data-testid="Products.list">
          {products.map((product, index) => {
            const image = resolveMediaUrl(product.imageUrl, apiBaseUrl());
            const isCurrent = product.id === currentProductId;
            return (
              <li
                key={product.id}
                data-testid="Products.row"
                data-product-id={product.id}
                data-product-name={product.name}
                draggable
                onDragStart={(e) => {
                  setDragId(product.id);
                  e.dataTransfer.effectAllowed = "move";
                  e.dataTransfer.setData("text/plain", product.id);
                }}
                onDragOver={(e) => {
                  if (!dragId || dragId === product.id) return;
                  e.preventDefault();
                  e.dataTransfer.dropEffect = "move";
                  setOverId(product.id);
                }}
                onDragLeave={() => setOverId((id) => (id === product.id ? null : id))}
                onDrop={(e) => {
                  e.preventDefault();
                  if (dragId && dragId !== product.id) {
                    applyOrder(moveById(products, dragId, product.id));
                  }
                  setDragId(null);
                  setOverId(null);
                }}
                onDragEnd={() => {
                  setDragId(null);
                  setOverId(null);
                }}
                className={`flex items-center gap-2 rounded-xl border bg-studio-card p-2 transition ${
                  overId === product.id
                    ? "border-amber"
                    : isCurrent
                      ? "border-live-edge"
                      : "border-studio-border"
                } ${dragId === product.id ? "opacity-50" : ""}`}
              >
                <span
                  className="cursor-grab text-studio-faint active:cursor-grabbing"
                  title="Kéo để sắp xếp"
                  aria-hidden
                >
                  <GripVertical className="h-4 w-4" />
                </span>
                <span className="w-5 text-center text-xs font-semibold text-studio-muted">
                  {index + 1}
                </span>
                <div className="h-12 w-12 shrink-0 overflow-hidden rounded-lg bg-studio-hover">
                  {image && (
                    <img
                      src={image}
                      alt=""
                      className="h-full w-full object-cover"
                      draggable={false}
                    />
                  )}
                </div>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-sm font-medium text-studio-fg">{product.name}</p>
                  <p className="truncate text-xs text-studio-muted">
                    {formatPrice(product.price, product.currency) || "Chưa có giá"}
                    {product.script ? "" : " · Chưa có kịch bản"}
                  </p>
                </div>
                <Badge tone={STATUS_TONE[product.status]}>
                  {PRODUCT_STATUS_LABEL[product.status]}
                </Badge>
                {canPresent && (
                  <Button
                    size="sm"
                    variant="live"
                    data-testid="Products.present"
                    onClick={() => onPresent(product.id)}
                    busy={presentingRequestId === product.id}
                  >
                    <Play className="h-3.5 w-3.5" /> Giới thiệu ngay
                  </Button>
                )}
                <div className="flex shrink-0 items-center">
                  <Button
                    size="sm"
                    variant="quiet"
                    aria-label={`Đưa ${product.name} lên`}
                    data-testid="Products.up"
                    disabled={index === 0 || reorder.isPending}
                    onClick={() => applyOrder(moveItem(products, index, index - 1))}
                  >
                    <ArrowUp className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    size="sm"
                    variant="quiet"
                    aria-label={`Đưa ${product.name} xuống`}
                    data-testid="Products.down"
                    disabled={index === products.length - 1 || reorder.isPending}
                    onClick={() => applyOrder(moveItem(products, index, index + 1))}
                  >
                    <ArrowDown className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    size="sm"
                    variant="quiet"
                    aria-label={`Sửa ${product.name}`}
                    data-testid="Products.edit"
                    onClick={() => setEditor({ product })}
                  >
                    <Pencil className="h-3.5 w-3.5" />
                  </Button>
                  <Button
                    size="sm"
                    variant="quiet"
                    aria-label={`Xóa ${product.name}`}
                    data-testid="Products.delete"
                    busy={remove.isPending && remove.variables === product.id}
                    onClick={() => {
                      if (window.confirm(`Xóa sản phẩm "${product.name}"?`)) {
                        remove.mutate(product.id);
                      }
                    }}
                  >
                    <Trash2 className="h-3.5 w-3.5" />
                  </Button>
                </div>
              </li>
            );
          })}
        </ol>
      )}

      {editor && (
        <ProductEditor
          key={editor.product?.id ?? "new"}
          sessionId={session.id}
          product={editor.product}
          onClose={() => setEditor(null)}
          onSaved={upsert}
        />
      )}
    </Panel>
  );
}
