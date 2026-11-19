"use client";

// Create or edit a catalog product: the facts the AI script may use and the
// photo Flow is given as the product reference.

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ImagePlus } from "lucide-react";
import { type FormEvent, useState } from "react";
import { apiBaseUrl } from "@/lib/api";
import { createCatalogProduct, updateCatalogProduct } from "@/lib/promo/api";
import { promoKeys } from "@/lib/promo/queryKeys";
import type { CatalogProduct, CatalogProductInput } from "@/lib/promo/types";
import { describeError, uploadImage } from "@/lib/studio/api";
import { resolveMediaUrl } from "@/lib/studio/logic";
import { Button, ErrorNote, Field, inputClass } from "@/components/studio/ui";

const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

function initialInput(product: CatalogProduct | null): CatalogProductInput {
  return {
    name: product?.name ?? "",
    brand: product?.brand ?? "",
    category: product?.category ?? "",
    price: product?.price ?? null,
    currency: product?.currency ?? "VND",
    description: product?.description ?? "",
    highlights: product?.highlights ?? [],
    usage: product?.usage ?? "",
    talkingPoints: product?.talkingPoints ?? "",
    imageUrl: product && !product.imageIsPlaceholder ? product.imageUrl : null,
  };
}

export function ProductForm({
  product,
  onDone,
  onCancel,
}: {
  product: CatalogProduct | null;
  onDone: (product: CatalogProduct) => void;
  onCancel: () => void;
}) {
  const qc = useQueryClient();
  const [input, setInput] = useState<CatalogProductInput>(() => initialInput(product));
  const [highlights, setHighlights] = useState(() => (product?.highlights ?? []).join("\n"));
  const [uploading, setUploading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const save = useMutation({
    mutationFn: async () => {
      const body = { ...input, highlights: highlights.split("\n") };
      return product ? updateCatalogProduct(product.id, body) : createCatalogProduct(body);
    },
    onSuccess: (saved) => {
      qc.invalidateQueries({ queryKey: promoKeys.catalog });
      onDone(saved);
    },
    onError: (err) => setError(describeError(err)),
  });

  const set = <K extends keyof CatalogProductInput>(key: K, value: CatalogProductInput[K]) =>
    setInput((prev) => ({ ...prev, [key]: value }));

  const onImage = async (file: File | undefined) => {
    if (!file) return;
    if (file.size > MAX_IMAGE_BYTES) {
      setError("Ảnh tối đa 8 MB.");
      return;
    }
    setUploading(true);
    setError(null);
    try {
      set("imageUrl", await uploadImage(file));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setUploading(false);
    }
  };

  const submit = (e: FormEvent) => {
    e.preventDefault();
    setError(null);
    if (!input.name.trim()) {
      setError("Hãy nhập tên sản phẩm.");
      return;
    }
    save.mutate();
  };

  const preview = resolveMediaUrl(input.imageUrl, apiBaseUrl());

  return (
    <form onSubmit={submit} className="flex flex-col gap-3" data-testid="ProductForm">
      <div className="flex gap-3">
        <label className="flex h-32 w-28 shrink-0 cursor-pointer flex-col items-center justify-center overflow-hidden rounded-xl border border-dashed border-studio-edge bg-studio-card text-xs text-studio-muted hover:bg-studio-hover">
          {preview ? (
            <img src={preview} alt="Ảnh sản phẩm" className="h-full w-full object-cover" />
          ) : (
            <>
              <ImagePlus className="mb-1 h-5 w-5" />
              {uploading ? "Đang tải..." : "Ảnh sản phẩm thật"}
            </>
          )}
          <input
            type="file"
            accept="image/png,image/jpeg,image/webp"
            className="sr-only"
            onChange={(e) => onImage(e.target.files?.[0])}
          />
        </label>
        <div className="flex min-w-0 flex-1 flex-col gap-3">
          <Field label="Tên sản phẩm" htmlFor="pf-name">
            <input
              id="pf-name"
              className={inputClass}
              value={input.name}
              onChange={(e) => set("name", e.target.value)}
              maxLength={200}
            />
          </Field>
          <div className="grid grid-cols-2 gap-3">
            <Field label="Thương hiệu" htmlFor="pf-brand">
              <input
                id="pf-brand"
                className={inputClass}
                value={input.brand}
                onChange={(e) => set("brand", e.target.value)}
                maxLength={120}
              />
            </Field>
            <Field label="Danh mục" htmlFor="pf-category">
              <input
                id="pf-category"
                className={inputClass}
                value={input.category}
                onChange={(e) => set("category", e.target.value)}
                maxLength={120}
              />
            </Field>
          </div>
        </div>
      </div>
      <p className="text-xs text-studio-faint">
        Ảnh được gửi cho Flow làm ảnh tham chiếu sản phẩm: dùng ảnh chụp thật, rõ nhãn và logo.
      </p>
      <div className="grid grid-cols-[1fr_6rem] gap-3">
        <Field label="Giá" htmlFor="pf-price">
          <input
            id="pf-price"
            type="number"
            min={0}
            className={inputClass}
            value={input.price ?? ""}
            onChange={(e) => set("price", e.target.value === "" ? null : Number(e.target.value))}
          />
        </Field>
        <Field label="Tiền tệ" htmlFor="pf-currency">
          <select
            id="pf-currency"
            className={inputClass}
            value={input.currency}
            onChange={(e) => set("currency", e.target.value)}
          >
            <option value="VND">VND</option>
            <option value="USD">USD</option>
          </select>
        </Field>
      </div>
      <Field label="Mô tả" htmlFor="pf-desc">
        <textarea
          id="pf-desc"
          rows={3}
          className={inputClass}
          value={input.description}
          onChange={(e) => set("description", e.target.value)}
          maxLength={4000}
        />
      </Field>
      <Field
        label="Đặc điểm nổi bật"
        hint="Mỗi dòng một ý. AI chỉ dùng những gì có ở đây."
        htmlFor="pf-hl"
      >
        <textarea
          id="pf-hl"
          rows={4}
          className={inputClass}
          value={highlights}
          onChange={(e) => setHighlights(e.target.value)}
        />
      </Field>
      <Field
        label="Cách dùng"
        hint="Người mẫu sẽ cầm và dùng sản phẩm đúng như mô tả này."
        htmlFor="pf-usage"
      >
        <textarea
          id="pf-usage"
          rows={2}
          className={inputClass}
          value={input.usage}
          onChange={(e) => set("usage", e.target.value)}
          maxLength={1000}
        />
      </Field>
      <Field
        label="Nội dung cần giới thiệu"
        hint="Những ý người mẫu nên nói: khuyến mãi, quà tặng, cam kết, đối tượng phù hợp. AI chỉ nói những gì có ở đây và ở các ô trên."
        htmlFor="pf-pitch"
      >
        <textarea
          id="pf-pitch"
          rows={3}
          className={inputClass}
          value={input.talkingPoints}
          onChange={(e) => set("talkingPoints", e.target.value)}
          maxLength={2000}
          placeholder="Ví dụ: Giảm 20% trong phiên live hôm nay, tặng kèm túi đựng, freeship đơn từ 199k."
        />
      </Field>
      <ErrorNote message={error} />
      <div className="flex justify-end gap-2">
        <Button variant="quiet" onClick={onCancel}>
          Hủy
        </Button>
        <Button type="submit" variant="primary" busy={save.isPending} disabled={uploading}>
          {product ? "Lưu sản phẩm" : "Thêm sản phẩm"}
        </Button>
      </div>
    </form>
  );
}
