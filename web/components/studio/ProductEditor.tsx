"use client";

// Add / edit dialog for one livestream product. Saving and AI script
// generation both hit the real API; the dialog only closes after the server
// accepted the change.

import { Sparkles, Upload, X } from "lucide-react";
import { type FormEvent, useEffect, useRef, useState } from "react";
import { apiBaseUrl } from "@/lib/api";
import {
  createProduct,
  describeError,
  generateProductScript,
  updateProduct,
  uploadImage,
} from "@/lib/studio/api";
import { parsePriceInput, resolveMediaUrl } from "@/lib/studio/logic";
import type { ProductInput, StudioProduct } from "@/lib/studio/types";
import { Button, ErrorNote, Field, inputClass } from "./ui";

const CURRENCIES = ["VND", "USD"];
const MAX_IMAGE_BYTES = 8 * 1024 * 1024;

export interface ProductEditorProps {
  sessionId: string;
  /** Null when adding a new product. */
  product: StudioProduct | null;
  onClose: () => void;
  /** Called with the server's copy after every successful write. */
  onSaved: (product: StudioProduct) => void;
}

function priceText(price: number | null): string {
  return price === null ? "" : String(price);
}

export function ProductEditor({ sessionId, product, onClose, onSaved }: ProductEditorProps) {
  // `saved` is the server copy; it becomes non-null once a new product has
  // been created (for example by the AI script button) so later saves patch.
  const [saved, setSaved] = useState<StudioProduct | null>(product);
  const [name, setName] = useState(product?.name ?? "");
  const [price, setPrice] = useState(priceText(product?.price ?? null));
  const [currency, setCurrency] = useState(product?.currency ?? "VND");
  const [description, setDescription] = useState(product?.description ?? "");
  const [highlights, setHighlights] = useState(product?.highlights ?? "");
  const [imageUrl, setImageUrl] = useState(product?.imageUrl ?? "");
  const [script, setScript] = useState(product?.script ?? "");
  const [busy, setBusy] = useState<"save" | "script" | "upload" | null>(null);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement | null>(null);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape" && busy === null) onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [busy, onClose]);

  const collect = (): ProductInput | null => {
    const trimmed = name.trim();
    if (!trimmed) {
      setError("Hãy nhập tên sản phẩm.");
      return null;
    }
    const parsed = parsePriceInput(price, currency);
    if (parsed === "invalid") {
      setError("Giá bán không hợp lệ. Chỉ nhập số, ví dụ 199000.");
      return null;
    }
    return {
      name: trimmed,
      price: parsed,
      currency,
      description: description.trim(),
      highlights: highlights.trim(),
      imageUrl: imageUrl.trim() || null,
      script: script.trim(),
    };
  };

  /** Create or patch, returning the server copy. */
  const persist = async (input: ProductInput): Promise<StudioProduct> => {
    const result = saved
      ? await updateProduct(saved.id, input)
      : await createProduct(sessionId, input);
    setSaved(result);
    onSaved(result);
    return result;
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError(null);
    const input = collect();
    if (!input) return;
    setBusy("save");
    try {
      await persist(input);
      onClose();
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(null);
    }
  };

  const writeScript = async () => {
    setError(null);
    const input = collect();
    if (!input) return;
    setBusy("script");
    try {
      // The server writes the script from the stored fields, so save first.
      const current = await persist(input);
      const withScript = await generateProductScript(current.id);
      setSaved(withScript);
      setScript(withScript.script);
      onSaved(withScript);
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(null);
    }
  };

  const upload = async (file: File | undefined) => {
    if (!file) return;
    setError(null);
    if (file.size > MAX_IMAGE_BYTES) {
      setError("Ảnh lớn hơn 8 MB. Hãy chọn ảnh nhỏ hơn.");
      return;
    }
    setBusy("upload");
    try {
      setImageUrl(await uploadImage(file));
    } catch (err) {
      setError(describeError(err));
    } finally {
      setBusy(null);
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const preview = resolveMediaUrl(imageUrl.trim() || null, apiBaseUrl());

  return (
    <div
      className="fixed inset-0 z-[60] flex items-start justify-center overflow-y-auto bg-black/70 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={saved ? "Sửa sản phẩm" : "Thêm sản phẩm"}
    >
      <form
        onSubmit={submit}
        data-testid="ProductEditor.form"
        className="my-6 flex w-full max-w-2xl flex-col gap-4 rounded-2xl border border-studio-edge bg-studio-panel p-5 text-studio-fg"
      >
        <header className="flex items-center justify-between">
          <h2 className="text-base font-semibold">{saved ? "Sửa sản phẩm" : "Thêm sản phẩm"}</h2>
          <Button
            variant="quiet"
            size="sm"
            onClick={onClose}
            disabled={busy !== null}
            aria-label="Đóng"
          >
            <X className="h-4 w-4" />
          </Button>
        </header>

        <div className="grid gap-4 sm:grid-cols-[140px_1fr]">
          <div className="flex flex-col gap-2">
            <div className="aspect-square w-full overflow-hidden rounded-xl border border-studio-border bg-studio-card">
              {preview ? (
                <img src={preview} alt="Ảnh sản phẩm" className="h-full w-full object-cover" />
              ) : (
                <div className="flex h-full items-center justify-center px-2 text-center text-xs text-studio-faint">
                  Chưa có ảnh
                </div>
              )}
            </div>
            <input
              ref={fileRef}
              type="file"
              accept="image/*"
              className="hidden"
              data-testid="ProductEditor.file"
              onChange={(e) => void upload(e.target.files?.[0])}
            />
            <Button size="sm" onClick={() => fileRef.current?.click()} busy={busy === "upload"}>
              <Upload className="h-3.5 w-3.5" /> Tải ảnh lên
            </Button>
          </div>

          <div className="flex flex-col gap-3">
            <Field label="Tên sản phẩm" htmlFor="product-name">
              <input
                id="product-name"
                data-testid="ProductEditor.name"
                value={name}
                onChange={(e) => setName(e.target.value)}
                maxLength={160}
                className={inputClass}
              />
            </Field>
            <div className="grid grid-cols-[1fr_110px] gap-3">
              <Field label="Giá bán" htmlFor="product-price">
                <input
                  id="product-price"
                  data-testid="ProductEditor.price"
                  value={price}
                  onChange={(e) => setPrice(e.target.value)}
                  inputMode="decimal"
                  placeholder="199000"
                  className={inputClass}
                />
              </Field>
              <Field label="Tiền tệ" htmlFor="product-currency">
                <select
                  id="product-currency"
                  value={currency}
                  onChange={(e) => setCurrency(e.target.value)}
                  className={inputClass}
                >
                  {(CURRENCIES.includes(currency) ? CURRENCIES : [currency, ...CURRENCIES]).map(
                    (c) => (
                      <option key={c} value={c}>
                        {c}
                      </option>
                    ),
                  )}
                </select>
              </Field>
            </div>
            <Field label="Hoặc dán đường dẫn ảnh" htmlFor="product-image-url">
              <input
                id="product-image-url"
                value={imageUrl}
                onChange={(e) => setImageUrl(e.target.value)}
                placeholder="https://..."
                className={inputClass}
              />
            </Field>
          </div>
        </div>

        <Field label="Mô tả" htmlFor="product-description">
          <textarea
            id="product-description"
            value={description}
            onChange={(e) => setDescription(e.target.value)}
            rows={3}
            className={inputClass}
          />
        </Field>
        <Field
          label="Thông tin nổi bật"
          htmlFor="product-highlights"
          hint="Mỗi dòng một ý: công dụng, chất liệu, ưu đãi, quà tặng."
        >
          <textarea
            id="product-highlights"
            value={highlights}
            onChange={(e) => setHighlights(e.target.value)}
            rows={3}
            className={inputClass}
          />
        </Field>
        <Field
          label="Kịch bản giới thiệu"
          htmlFor="product-script"
          hint="Người mẫu dựa vào kịch bản này nhưng diễn đạt lại tự nhiên, không đọc nguyên văn."
        >
          <textarea
            id="product-script"
            data-testid="ProductEditor.script"
            value={script}
            onChange={(e) => setScript(e.target.value)}
            rows={6}
            className={inputClass}
          />
        </Field>

        <ErrorNote message={error} />

        <footer className="flex flex-wrap items-center justify-between gap-2">
          <Button
            onClick={() => void writeScript()}
            busy={busy === "script"}
            disabled={busy !== null}
            data-testid="ProductEditor.aiScript"
          >
            <Sparkles className="h-4 w-4" />
            {saved ? "Tạo kịch bản bằng AI" : "Lưu và tạo kịch bản bằng AI"}
          </Button>
          <div className="flex gap-2">
            <Button variant="quiet" onClick={onClose} disabled={busy !== null}>
              Hủy
            </Button>
            <Button
              type="submit"
              variant="primary"
              busy={busy === "save"}
              disabled={busy !== null}
              data-testid="ProductEditor.save"
            >
              {saved ? "Lưu sản phẩm" : "Thêm sản phẩm"}
            </Button>
          </div>
        </footer>
      </form>
    </div>
  );
}
