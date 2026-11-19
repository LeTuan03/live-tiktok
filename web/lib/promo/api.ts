// Typed client for `/catalog/*` and `/promo/*`. The only module where the
// snake_case wire format is visible.

import { apiBaseUrl } from "@/lib/api";
import { request } from "@/lib/studio/api";
import type {
  CatalogProduct,
  CatalogProductInput,
  FlowStatus,
  PromoLanguage,
  PromoScript,
  PromoShot,
  PromoStatus,
  PromoVideo,
  ShotBeat,
  ShotState,
  ShotStatus,
} from "./types";

// ---------------------------------------------------------------- wire types

export interface WireCatalogProduct {
  id: string;
  sku: string;
  name: string;
  brand?: string | null;
  category?: string | null;
  price?: number | null;
  currency?: string | null;
  description?: string | null;
  highlights?: string[] | null;
  usage?: string | null;
  talking_points?: string | null;
  image_url?: string | null;
  gallery?: string[] | null;
  image_is_placeholder?: boolean | null;
}

export interface WireScript {
  angle: string;
  product_look: string;
  setting: string;
  caption?: string | null;
  shots: PromoShot[];
}

export interface WireVideo {
  id: string;
  product: { id: string; name: string; image_url?: string | null };
  host: { name: string; image_url?: string | null };
  language: string;
  shot_count: number;
  flow_model: string;
  script?: WireScript | null;
  status: string;
  stage?: string | null;
  error?: string | null;
  shots?:
    | {
        index: number;
        status: string;
        error?: string | null;
        has_clip?: boolean;
        transcript?: string | null;
        speech_match?: number | null;
      }[]
    | null;
  running?: boolean;
  has_video?: boolean;
  has_thumb?: boolean;
  credits_estimate?: number;
  duration_s?: number | null;
  created_at: string;
  updated_at: string;
}

interface WireFlowStatus {
  connected: boolean;
  extension_version?: string | null;
  flow_tabs?: number | null;
  running_job_id?: string | null;
  default_model: string;
  models: { label: string; credits: number; retiring?: string | null }[];
}

// ------------------------------------------------------------------ adapters

const STATUSES: PromoStatus[] = ["draft", "queued", "rendering", "assembling", "ready", "failed"];
const SHOT_STATUSES: ShotStatus[] = [
  "pending",
  "submitting",
  "generating",
  "downloading",
  "done",
  "failed",
];
const BEATS: ShotBeat[] = ["hook", "demo", "benefit", "cta"];

export function adaptProduct(wire: WireCatalogProduct): CatalogProduct {
  return {
    id: wire.id,
    sku: wire.sku,
    name: wire.name,
    brand: wire.brand ?? "",
    category: wire.category ?? "",
    price: typeof wire.price === "number" ? wire.price : null,
    currency: (wire.currency ?? "VND").toUpperCase(),
    description: wire.description ?? "",
    highlights: wire.highlights ?? [],
    usage: wire.usage ?? "",
    talkingPoints: wire.talking_points ?? "",
    imageUrl: wire.image_url ?? null,
    gallery: wire.gallery ?? [],
    imageIsPlaceholder: wire.image_is_placeholder ?? !wire.image_url,
  };
}

export function adaptScript(wire: WireScript): PromoScript {
  return {
    angle: wire.angle,
    productLook: wire.product_look,
    setting: wire.setting,
    caption: wire.caption ?? "",
    shots: wire.shots.map((s) => ({
      ...s,
      beat: BEATS.includes(s.beat) ? s.beat : "demo",
    })),
  };
}

export function scriptToWire(script: PromoScript): WireScript {
  return {
    angle: script.angle,
    product_look: script.productLook,
    setting: script.setting,
    caption: script.caption,
    shots: script.shots,
  };
}

export function adaptVideo(wire: WireVideo): PromoVideo {
  const shots: ShotState[] = (wire.shots ?? []).map((s) => ({
    index: s.index,
    status: SHOT_STATUSES.includes(s.status as ShotStatus) ? (s.status as ShotStatus) : "pending",
    error: s.error ?? null,
    hasClip: !!s.has_clip,
    transcript: s.transcript ?? null,
    speechMatch: typeof s.speech_match === "number" ? s.speech_match : null,
  }));
  return {
    id: wire.id,
    productId: wire.product.id,
    productName: wire.product.name,
    productImageUrl: wire.product.image_url ?? null,
    hostName: wire.host.name,
    hostImageUrl: wire.host.image_url ?? null,
    language: wire.language === "en" ? "en" : "vi",
    shotCount: wire.shot_count,
    flowModel: wire.flow_model,
    script: wire.script ? adaptScript(wire.script) : null,
    status: STATUSES.includes(wire.status as PromoStatus) ? (wire.status as PromoStatus) : "failed",
    stage: wire.stage ?? "",
    error: wire.error ?? null,
    shots,
    running: !!wire.running,
    hasVideo: !!wire.has_video,
    hasThumb: !!wire.has_thumb,
    creditsEstimate: wire.credits_estimate ?? 0,
    durationS: wire.duration_s ?? null,
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
  };
}

export function productToWire(input: Partial<CatalogProductInput>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (input.name !== undefined) out.name = input.name.trim();
  if (input.brand !== undefined) out.brand = input.brand.trim();
  if (input.category !== undefined) out.category = input.category.trim();
  if (input.price !== undefined) out.price = input.price;
  if (input.currency !== undefined) out.currency = input.currency.toUpperCase();
  if (input.description !== undefined) out.description = input.description.trim();
  if (input.highlights !== undefined)
    out.highlights = input.highlights.map((h) => h.trim()).filter(Boolean);
  if (input.usage !== undefined) out.usage = input.usage.trim();
  if (input.talkingPoints !== undefined) out.talking_points = input.talkingPoints.trim();
  if (input.imageUrl !== undefined && input.imageUrl !== null) out.image_url = input.imageUrl;
  return out;
}

// ------------------------------------------------------------------- calls

const enc = encodeURIComponent;

export async function listCatalog(): Promise<CatalogProduct[]> {
  const data = await request<{ products: WireCatalogProduct[] }>("GET", "/catalog/products");
  return data.products.map(adaptProduct);
}

export async function createCatalogProduct(input: CatalogProductInput): Promise<CatalogProduct> {
  return adaptProduct(
    await request<WireCatalogProduct>("POST", "/catalog/products", { json: productToWire(input) }),
  );
}

export async function updateCatalogProduct(
  id: string,
  input: Partial<CatalogProductInput>,
): Promise<CatalogProduct> {
  return adaptProduct(
    await request<WireCatalogProduct>("PATCH", `/catalog/products/${enc(id)}`, {
      json: productToWire(input),
    }),
  );
}

export async function deleteCatalogProduct(id: string): Promise<void> {
  await request<void>("DELETE", `/catalog/products/${enc(id)}`);
}

export async function getFlowStatus(): Promise<FlowStatus> {
  const w = await request<WireFlowStatus>("GET", "/promo/flow/status");
  return {
    connected: w.connected,
    extensionVersion: w.extension_version ?? null,
    flowTabs: w.flow_tabs ?? null,
    runningJobId: w.running_job_id ?? null,
    defaultModel: w.default_model,
    models: w.models.map((m) => ({
      label: m.label,
      credits: m.credits,
      retiring: m.retiring ?? null,
    })),
  };
}

export async function listVideos(productId?: string): Promise<PromoVideo[]> {
  const query = productId ? `?product_id=${enc(productId)}` : "";
  const data = await request<{ videos: WireVideo[] }>("GET", `/promo/videos${query}`);
  return data.videos.map(adaptVideo);
}

export async function getVideo(id: string): Promise<PromoVideo> {
  return adaptVideo(await request<WireVideo>("GET", `/promo/videos/${enc(id)}`));
}

export async function createVideo(input: {
  productId: string;
  modelId: string;
  shots: number;
  language: PromoLanguage;
  model: string;
}): Promise<PromoVideo> {
  const json = {
    product_id: input.productId,
    model_id: input.modelId,
    shots: input.shots,
    language: input.language,
    model: input.model,
  };
  return adaptVideo(await request<WireVideo>("POST", "/promo/videos", { json }));
}

export async function regenerateScript(id: string): Promise<PromoVideo> {
  return adaptVideo(await request<WireVideo>("POST", `/promo/videos/${enc(id)}/script`));
}

export async function saveScript(id: string, script: PromoScript): Promise<PromoVideo> {
  return adaptVideo(
    await request<WireVideo>("PUT", `/promo/videos/${enc(id)}/script`, {
      json: { script: scriptToWire(script) },
    }),
  );
}

export async function renderVideo(id: string): Promise<PromoVideo> {
  return adaptVideo(await request<WireVideo>("POST", `/promo/videos/${enc(id)}/render`));
}

export async function regenerateShot(id: string, index: number): Promise<PromoVideo> {
  return adaptVideo(
    await request<WireVideo>("POST", `/promo/videos/${enc(id)}/shots/${index}/regenerate`),
  );
}

export async function cancelVideo(id: string): Promise<PromoVideo> {
  return adaptVideo(await request<WireVideo>("POST", `/promo/videos/${enc(id)}/cancel`));
}

export async function deleteVideo(id: string): Promise<void> {
  await request<void>("DELETE", `/promo/videos/${enc(id)}`);
}

/** Absolute URLs for the files a video exposes; `v` busts the browser cache. */
export function videoUrls(video: Pick<PromoVideo, "id" | "updatedAt">) {
  const base = `${apiBaseUrl()}/promo/videos/${enc(video.id)}`;
  const v = `v=${enc(video.updatedAt)}`;
  return {
    file: `${base}/file?${v}`,
    thumb: `${base}/thumb?${v}`,
    captions: `${base}/captions?${v}`,
    debug: `${base}/debug?${v}`,
    clip: (index: number) => `${base}/shots/${index}/clip?${v}`,
  };
}
