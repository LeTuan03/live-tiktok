// Listens on the LiveKit `product_pitch` text-stream topic and re-emits
// typed events to subscribers. The agent (server) publishes via
// `room.local_participant.send_text(payload, topic="product_pitch")`
// — the canonical web consumer is `room.registerTextStreamHandler(...)`.
//
// Wire shape (from `server/src/soshop/agent/tools.py`, do NOT change):
//   { "type": "show",    "product_id": "<uuid>", "popup": { ... } }
//   { "type": "dismiss", "product_id": "<uuid>" }
//
// React-agnostic. The hook in `useFeedPitchEvents` is a thin adapter.

import type { Room } from "livekit-client";

export const PRODUCT_PITCH_TOPIC = "product_pitch";

export interface PitchPopupMediaItem {
  position: number;
  kind: "image" | "video" | string;
  url: string;
}

export interface PitchPopupPayload {
  id?: string;
  title?: string;
  image_url?: string;
  cta?: string;
  deep_link_url?: string;
  landing_url?: string;
  /** Ordered gallery of every product_media row for this product —
   *  position 0 is the hero image, position 1 is conventionally the
   *  feature video, positions ≥2 are lifestyle/detail images. The
   *  product-focus surface renders this as a paginated carousel. May
   *  be empty when the product has no gallery rows. */
  media?: PitchPopupMediaItem[];
  [key: string]: unknown;
}

export type PitchEvent =
  | { kind: "show"; productId: string; popup: PitchPopupPayload | null }
  | { kind: "dismiss"; productId: string };

export type PitchEventListener = (event: PitchEvent) => void;

interface ParsedPayload {
  type?: unknown;
  product_id?: unknown;
  popup?: unknown;
}

function parsePitchEvent(text: string): PitchEvent | null {
  let raw: ParsedPayload;
  try {
    raw = JSON.parse(text) as ParsedPayload;
  } catch {
    return null;
  }
  const type = typeof raw.type === "string" ? raw.type : null;
  const productId = typeof raw.product_id === "string" ? raw.product_id : null;
  if (!type || !productId) return null;
  if (type === "show") {
    const popup =
      raw.popup && typeof raw.popup === "object" ? (raw.popup as PitchPopupPayload) : null;
    return { kind: "show", productId, popup };
  }
  if (type === "dismiss") {
    return { kind: "dismiss", productId };
  }
  return null;
}

/** Minimal `Room`-shaped surface PitchEventSource needs. Lets tests
 *  inject a fake without pulling the full SDK. */
export interface PitchTextStreamRoom {
  registerTextStreamHandler: Room["registerTextStreamHandler"];
  unregisterTextStreamHandler: Room["unregisterTextStreamHandler"];
}

export class PitchEventSource {
  private readonly listeners = new Set<PitchEventListener>();
  private room: PitchTextStreamRoom | null = null;
  private disposed = false;

  attach(room: PitchTextStreamRoom): void {
    if (this.disposed) return;
    if (this.room === room) return;
    if (this.room) this.detach();
    this.room = room;
    room.registerTextStreamHandler(PRODUCT_PITCH_TOPIC, async (reader) => {
      const text = await reader.readAll();
      const event = parsePitchEvent(text);
      if (!event) return;
      for (const listener of this.listeners) listener(event);
    });
  }

  detach(): void {
    if (!this.room) return;
    try {
      this.room.unregisterTextStreamHandler(PRODUCT_PITCH_TOPIC);
    } catch {
      // Already unregistered (Room teardown). Swallow — detach is idempotent.
    }
    this.room = null;
  }

  subscribe(listener: PitchEventListener): () => void {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  }

  /** Test hook: synthesise an event as if it had arrived from the agent. */
  emitForTest(event: PitchEvent): void {
    for (const listener of this.listeners) listener(event);
  }

  dispose(): void {
    this.disposed = true;
    this.detach();
    this.listeners.clear();
  }
}
