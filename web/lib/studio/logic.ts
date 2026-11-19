// Pure helpers for the studio UI: list reordering, formatting, live-state
// merging and control availability. No React, no network, so they are unit
// tested directly.

import type { LiveMode, LiveState, ProductStatus, SessionStatus } from "./types";

/** Return a copy of `items` with the element at `from` moved to `to`.
 *  Out-of-range or no-op moves return the original array. */
export function moveItem<T>(items: readonly T[], from: number, to: number): T[] {
  const copy = [...items];
  if (from === to || from < 0 || to < 0 || from >= copy.length || to >= copy.length) return copy;
  const [moved] = copy.splice(from, 1);
  copy.splice(to, 0, moved);
  return copy;
}

/** Move the item with `id` so it lands at the position currently held by
 *  `targetId` (drag-and-drop semantics). */
export function moveById<T extends { id: string }>(
  items: readonly T[],
  id: string,
  targetId: string,
): T[] {
  const from = items.findIndex((item) => item.id === id);
  const to = items.findIndex((item) => item.id === targetId);
  return moveItem(items, from, to);
}

/** True when both lists hold the same ids in the same order. */
export function sameOrder(a: readonly { id: string }[], b: readonly { id: string }[]): boolean {
  return a.length === b.length && a.every((item, i) => item.id === b[i].id);
}

/** Resolve an image reference. The API returns either an absolute URL or a
 *  path relative to the API origin (for example "/media/uploads/a.jpg"). */
export function resolveMediaUrl(url: string | null | undefined, apiBase: string): string | null {
  if (!url) return null;
  if (/^(https?:|data:|blob:)/i.test(url)) return url;
  const base = apiBase.replace(/\/$/, "");
  return `${base}${url.startsWith("/") ? url : `/${url}`}`;
}

export function formatPrice(price: number | null, currency: string): string {
  if (price === null || Number.isNaN(price)) return "";
  const code = (currency || "VND").toUpperCase();
  try {
    return new Intl.NumberFormat("vi-VN", {
      style: "currency",
      currency: code,
      maximumFractionDigits: code === "VND" ? 0 : 2,
    }).format(price);
  } catch {
    return `${price.toLocaleString("vi-VN")} ${code}`;
  }
}

/** Parse the price text box. Accepts "199000", "199.000", "199,000" and
 *  "12.5" (for currencies with decimals). Empty means no price. */
export function parsePriceInput(raw: string, currency: string): number | null | "invalid" {
  const text = raw.trim().replace(/\s/g, "");
  if (!text) return null;
  let normalized: string;
  if ((currency || "VND").toUpperCase() === "VND") {
    // The dong has no minor unit, so every separator is a thousands separator.
    normalized = text.replace(/[.,]/g, "");
  } else if (/^\d{1,3}(,\d{3})+(\.\d+)?$/.test(text)) {
    normalized = text.replace(/,/g, "");
  } else {
    normalized = text.replace(",", ".");
  }
  if (!/^\d+(\.\d+)?$/.test(normalized)) return "invalid";
  const value = Number(normalized);
  return Number.isFinite(value) && value >= 0 ? value : "invalid";
}

function stamp(state: LiveState | null): number {
  if (!state?.updatedAt) return 0;
  const t = Date.parse(state.updatedAt);
  return Number.isNaN(t) ? 0 : t;
}

/** Pick the freshest live state. The polled value is the source of truth; a
 *  state pushed over the room only wins while it is strictly newer. */
export function pickLiveState(
  polled: LiveState | null,
  pushed: LiveState | null,
): LiveState | null {
  if (!pushed) return polled;
  if (!polled) return pushed;
  return stamp(pushed) > stamp(polled) ? pushed : polled;
}

export interface ControlAvailability {
  startProducts: boolean;
  pause: boolean;
  resume: boolean;
  skip: boolean;
  stopProducts: boolean;
  present: boolean;
  say: boolean;
}

const NONE: ControlAvailability = {
  startProducts: false,
  pause: false,
  resume: false,
  skip: false,
  stopProducts: false,
  present: false,
  say: false,
};

/** Which live controls make sense for the current state. The server remains
 *  the authority (it answers 409 when a control does not apply); this only
 *  keeps buttons that cannot work from being clickable. */
export function controlAvailability(
  status: SessionStatus,
  live: LiveState | null,
  productCount: number,
): ControlAvailability {
  if (status !== "live" || !live) return NONE;
  const mode: LiveMode = live.mode;
  if (mode === "starting" || mode === "ending") return NONE;
  const hasProducts = productCount > 0;
  const onProduct = live.currentProductId !== null;
  return {
    startProducts: mode === "chatting" && hasProducts,
    pause: mode === "chatting" || mode === "presenting",
    resume: mode === "paused",
    skip: mode === "presenting" || (mode === "paused" && onProduct),
    stopProducts: mode === "presenting" || (mode === "paused" && onProduct),
    present: hasProducts,
    say: mode !== "paused",
  };
}

export const MODE_LABEL: Record<LiveMode, string> = {
  starting: "Đang khởi động",
  chatting: "Đang trò chuyện",
  presenting: "Đang giới thiệu sản phẩm",
  paused: "Đang tạm dừng",
  ending: "Đang kết thúc",
};

export const PRODUCT_STATUS_LABEL: Record<ProductStatus, string> = {
  pending: "Chờ giới thiệu",
  presenting: "Đang giới thiệu",
  done: "Đã giới thiệu",
  skipped: "Đã bỏ qua",
};

export const SESSION_STATUS_LABEL: Record<SessionStatus, string> = {
  draft: "Bản nháp",
  live: "Đang live",
  ended: "Đã kết thúc",
};
