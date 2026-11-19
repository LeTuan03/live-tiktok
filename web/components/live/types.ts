export interface LiveTokenResponse {
  url: string;
  token: string;
  roomState?: LiveRoomState;
}

export type LiveRoomState = "scheduled" | "live" | "ended";

/**
 * Pinned-product wire shape.
 *
 * An agent-driven pitch carries a free-form `cta` string (e.g.
 * "Tap to shop · $18") and both a `deepLinkUrl` and a `landingUrl`.
 * Catalog-driven pins instead carry a structured `priceCents` +
 * `currency` + `ctaLabel`. The structured fields are optional so either
 * origin drops into the same renderer, which renders the same human
 * payload with different field availabilities depending on origin.
 */
/** One slot in a product's ordered media gallery. Mirrors the
 *  `product_media` table. Position 0 is the hero image, position 1 is
 *  conventionally the feature video, positions ≥2 are lifestyle/detail
 *  images. The product-focus surface renders these as a paginated
 *  horizontal carousel. */
export interface PinnedProductMedia {
  position: number;
  kind: "image" | "video";
  url: string;
}

export interface PinnedProduct {
  productId: string;
  title: string;
  imageUrl?: string;
  /** Full gallery (image + video slots, position-sorted). Empty when
   *  the product has no `product_media` rows — the focus surface then
   *  falls back to a single hero render of `imageUrl`. */
  media?: PinnedProductMedia[];
  /** Optional structured price; preferred over `cta` when present. */
  priceCents?: number;
  /** ISO currency code; required when `priceCents` is set. */
  currency?: string;
  /** Free-form CTA copy ("Tap to shop · $18"). */
  cta?: string;
  /** Tap target; falls back to `/products/<productId>`. A pitch can send
   *  both `deepLinkUrl` (in-app) and `landingUrl` (web-fallback);
   *  the web client uses `landingUrl` if present, then `deepLinkUrl`,
   *  then the legacy `ctaUrl`. */
  ctaUrl?: string;
  deepLinkUrl?: string;
  landingUrl?: string;
  /** Legacy structured CTA label ("Shop now" / "Bid"). */
  ctaLabel?: string;
  pinnedAt: number;
}

export interface RoomStateEnvelope {
  kind: "room.state";
  state: LiveRoomState;
  startsAt?: number;
  endedAt?: number;
}

export interface ProductPinEnvelope {
  kind: "product.pin";
  product: PinnedProduct | null;
}

export interface ViewerCountEnvelope {
  kind: "viewer.count";
  count: number;
}

export type LiveDataEnvelope =
  | RoomStateEnvelope
  | ProductPinEnvelope
  | ViewerCountEnvelope
  | { kind: string; [key: string]: unknown };

export type ConnectionPhase =
  | "loading"
  | "scheduled"
  | "connecting"
  | "connected"
  | "ended"
  | "lost"
  | "error";
