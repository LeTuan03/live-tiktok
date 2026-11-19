// Channel-ring API client. Mirrors the `channels` table defined in
// migration 0001_initial_schema — one always-on LiveKit room per
// channel ordinal, each airing an `(advertiser × persona × product)`
// rotation. The web client never writes to channels (that's the agent
// loop's job); it only reads:
//   - `GET /channels` for the bootstrap channel list + viewer tokens,
//   - `GET /channels/{id}/current` for the active-ad overlay.
//
// Wire shape is snake_case as the server emits it; an `adapt*` helper
// per endpoint projects it into camelCase so consumer components stay
// in the JS idiom. No `npm run gen:api` codegen exists yet — this file
// is the canonical hand-mirror of the Pydantic response models in
// `server/src/soshop/api/routes/channels.py`. See `web/CLAUDE.md`
// → "Regenerating API types" for the regeneration recipe when the
// server schema changes.

import { authedFetch } from "@/lib/api";

// MARK: - Wire (snake_case, server-emitted)

interface ProductMediaWire {
  position: number;
  kind: string;
  url: string;
}

/** Server `AdSnapshot` from the channels route. */
interface AdSnapshotWire {
  id: string;
  product: {
    id: string;
    title: string;
    price_cents: number | null;
    currency: string;
    image_url: string | null;
    deep_link_url: string | null;
    landing_url: string | null;
    media: ProductMediaWire[];
  };
  persona: {
    id: string;
    display_name: string | null;
    avatar_url: string | null;
  };
  advertiser: {
    id: string;
    name: string;
  };
  deep_link_url: string | null;
  started_at: string;
  duration_ms: number;
}

/** Server `ChannelOut` from `GET /channels`. */
interface ChannelOutWire {
  id: string;
  ordinal: number;
  livekit_url: string;
  livekit_token: string;
  current_ad: AdSnapshotWire | null;
}

// `GET /channels/{id}/current` returns `CurrentAdResponse(ad=AdSnapshot)`
// — an envelope around the same `AdSnapshot` model the channels list
// emits. The previous hand-mirror typed a flat `{ ad_id, product,
// persona, ... }` shape that didn't match the Pydantic model at all,
// so `adaptCurrentAd` was silently producing `null` for product /
// persona on every call. Aligning to the actual envelope here so the
// shop-CTA overlay and the FeedPager mid-pitch seed path both get
// real data.
interface CurrentAdWire {
  ad: AdSnapshotWire | null;
}

// MARK: - Domain (camelCase)

export interface ChannelAdProductMedia {
  position: number;
  kind: string;
  url: string;
}

export interface ChannelAdProduct {
  id: string;
  title: string;
  priceCents: number | null;
  currency: string;
  imageUrl: string | null;
  deepLinkUrl: string | null;
  landingUrl: string | null;
  /** Ordered gallery — image kind only. Empty when the product has no
   *  `product_media` rows; the consumer feed falls back to a single
   *  hero slide built from `imageUrl` in that case. */
  media: ChannelAdProductMedia[];
}

export interface ChannelAdPersona {
  id: string;
  displayName: string | null;
  avatarUrl: string | null;
}

export interface ChannelAdAdvertiser {
  id: string;
  name: string;
}

export interface ChannelAdSnapshot {
  id: string;
  product: ChannelAdProduct;
  persona: ChannelAdPersona;
  advertiser: ChannelAdAdvertiser;
  deepLinkUrl: string | null;
  startedAt: string;
  durationMs: number;
}

/**
 * One running channel as returned by `GET /channels`.
 *
 * Field names match the subset of `ChannelRoomCoordinator`'s input
 * exactly so a caller can pass the array straight into `init()`. The
 * `currentAd` slot is the most-recently-logged ad on the channel and is
 * informational for the bootstrap — components that need a live overlay
 * still poll `getCurrentAd()` per active channel.
 */
export interface ChannelInfo {
  id: string;
  ordinal: number;
  livekitUrl: string;
  livekitToken: string;
  currentAd: ChannelAdSnapshot | null;
}

export interface CurrentAdProduct {
  id: string;
  title: string;
  priceCents: number | null;
  currency: string;
  imageUrl: string | null;
  deepLinkUrl: string | null;
  landingUrl: string | null;
  media: ChannelAdProductMedia[];
}

export interface CurrentAdPersona {
  id: string;
  displayName: string | null;
  avatarUrl: string | null;
}

export interface CurrentAdAdvertiser {
  id: string;
  name: string;
}

export interface CurrentAd {
  adId: string | null;
  product: CurrentAdProduct | null;
  persona: CurrentAdPersona | null;
  advertiser: CurrentAdAdvertiser | null;
  startedAt: string | null;
  durationMs: number | null;
}

// MARK: - Adapters

function adaptProductMedia(wire: ProductMediaWire[] | null | undefined): ChannelAdProductMedia[] {
  if (!wire) return [];
  return wire.map((m) => ({
    position: m.position,
    kind: m.kind,
    url: m.url,
  }));
}

function adaptAdSnapshot(wire: AdSnapshotWire): ChannelAdSnapshot {
  return {
    id: wire.id,
    product: {
      id: wire.product.id,
      title: wire.product.title,
      priceCents: wire.product.price_cents,
      currency: wire.product.currency,
      imageUrl: wire.product.image_url,
      deepLinkUrl: wire.product.deep_link_url,
      landingUrl: wire.product.landing_url,
      media: adaptProductMedia(wire.product.media),
    },
    persona: {
      id: wire.persona.id,
      displayName: wire.persona.display_name,
      avatarUrl: wire.persona.avatar_url,
    },
    advertiser: {
      id: wire.advertiser.id,
      name: wire.advertiser.name,
    },
    deepLinkUrl: wire.deep_link_url,
    startedAt: wire.started_at,
    durationMs: wire.duration_ms,
  };
}

function adaptChannel(wire: ChannelOutWire): ChannelInfo {
  return {
    id: wire.id,
    ordinal: wire.ordinal,
    livekitUrl: wire.livekit_url,
    livekitToken: wire.livekit_token,
    currentAd: wire.current_ad ? adaptAdSnapshot(wire.current_ad) : null,
  };
}

function adaptCurrentAd(wire: CurrentAdWire): CurrentAd {
  const ad = wire.ad;
  if (!ad) {
    return {
      adId: null,
      product: null,
      persona: null,
      advertiser: null,
      startedAt: null,
      durationMs: null,
    };
  }
  return {
    adId: ad.id,
    product: {
      id: ad.product.id,
      title: ad.product.title,
      priceCents: ad.product.price_cents,
      currency: ad.product.currency,
      imageUrl: ad.product.image_url,
      deepLinkUrl: ad.product.deep_link_url,
      landingUrl: ad.product.landing_url,
      media: adaptProductMedia(ad.product.media),
    },
    persona: {
      id: ad.persona.id,
      displayName: ad.persona.display_name,
      avatarUrl: ad.persona.avatar_url,
    },
    advertiser: { id: ad.advertiser.id, name: ad.advertiser.name },
    startedAt: ad.started_at,
    durationMs: ad.duration_ms,
  };
}

// MARK: - Endpoints

/**
 * List every running channel together with a fresh subscribe-only
 * LiveKit token. The consumer feed is unauthenticated, so no bearer token
 * is sent — the server mints an `anon-<uuid>` identity per token.
 *
 * Returns the channels in `ordinal` order (the server `ORDER BY`s
 * already, but consumers should not depend on call-site preservation).
 */
export async function listChannels(signal?: AbortSignal): Promise<ChannelInfo[]> {
  const res = await authedFetch("/channels", {
    method: "GET",
    signal,
    token: null,
  });
  const data = (await res.json()) as ChannelOutWire[];
  return data.map(adaptChannel);
}

/**
 * Fetch the ad currently airing on `channelId`.
 *
 * The consumer feed is unauthenticated, so no token is sent. The server
 * may answer 204 No Content (no ad airing); the caller receives `null`
 * in that case so the overlay can render an empty/loading state without
 * branching on response codes.
 */
export async function getCurrentAd(
  channelId: string,
  signal?: AbortSignal,
): Promise<CurrentAd | null> {
  const res = await authedFetch(`/channels/${encodeURIComponent(channelId)}/current`, {
    method: "GET",
    signal,
    token: null,
  });
  if (res.status === 204) return null;
  const data = (await res.json()) as CurrentAdWire;
  return adaptCurrentAd(data);
}
