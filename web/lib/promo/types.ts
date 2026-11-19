// Domain types for the promo video flow (catalog -> AI script -> Google Flow
// -> finished video). camelCase; `api.ts` adapts the snake_case wire format.

export type PromoLanguage = "vi" | "en";

export interface CatalogProduct {
  id: string;
  sku: string;
  name: string;
  brand: string;
  category: string;
  price: number | null;
  currency: string;
  description: string;
  highlights: string[];
  usage: string;
  /** What the shop wants said: promotions, gifts, guarantees. */
  talkingPoints: string;
  imageUrl: string | null;
  gallery: string[];
  /** True when the only image is a random stock placeholder, not the product. */
  imageIsPlaceholder: boolean;
}

export interface CatalogProductInput {
  name: string;
  brand: string;
  category: string;
  price: number | null;
  currency: string;
  description: string;
  highlights: string[];
  usage: string;
  talkingPoints: string;
  imageUrl: string | null;
}

export type ShotBeat = "hook" | "demo" | "benefit" | "cta";

export interface PromoShot {
  beat: ShotBeat;
  action: string;
  camera: string;
  emotion: string;
  dialogue: string;
}

export interface PromoScript {
  angle: string;
  productLook: string;
  setting: string;
  caption: string;
  shots: PromoShot[];
}

export type PromoStatus = "draft" | "queued" | "rendering" | "assembling" | "ready" | "failed";
export type ShotStatus =
  | "pending"
  | "submitting"
  | "generating"
  | "downloading"
  | "done"
  | "failed";

export interface ShotState {
  index: number;
  status: ShotStatus;
  error: string | null;
  hasClip: boolean;
  /** What Whisper heard in the clip. */
  transcript: string | null;
  /** Share of the scripted words heard, in order (0 to 1). */
  speechMatch: number | null;
}

export interface PromoVideo {
  id: string;
  productId: string;
  productName: string;
  productImageUrl: string | null;
  hostName: string;
  hostImageUrl: string | null;
  language: PromoLanguage;
  shotCount: number;
  flowModel: string;
  script: PromoScript | null;
  status: PromoStatus;
  stage: string;
  error: string | null;
  shots: ShotState[];
  running: boolean;
  hasVideo: boolean;
  hasThumb: boolean;
  creditsEstimate: number;
  durationS: number | null;
  createdAt: string;
  updatedAt: string;
}

export interface FlowModel {
  label: string;
  credits: number;
  retiring: string | null;
}

export interface FlowStatus {
  connected: boolean;
  extensionVersion: string | null;
  flowTabs: number | null;
  runningJobId: string | null;
  defaultModel: string;
  models: FlowModel[];
}
