// Domain types for livestream sessions played from pre-rendered videos
// ("phiên livestream"). camelCase; `api.ts` adapts the snake_case wire format.

import type { LoopMode, Playhead, Position } from "./timeline";

export type { LoopMode, Playhead, Position } from "./timeline";

export type BroadcastStatus = "idle" | "live" | "paused" | "ended";
export type ItemRole = "intro" | "product" | "outro";
export type ItemSource = "promo" | "upload";
export type ControlAction =
  | "start"
  | "pause"
  | "resume"
  | "next"
  | "prev"
  | "jump"
  | "finish"
  | "stop";

export interface ItemProduct {
  id: string;
  name: string;
  price: number | null;
  currency: string;
  imageUrl: string | null;
}

export interface BroadcastItem {
  id: string;
  role: ItemRole;
  position: number;
  source: ItemSource;
  sourceRef: string;
  label: string;
  /** What to call the item on screen: its label, else the product name. */
  title: string;
  /** Time on air set by the user; `null` plays the video once. */
  durationS: number | null;
  /** The video's own length. */
  videoS: number | null;
  /** Effective time on air; `null` while the item cannot play. */
  slotS: number | null;
  playable: boolean;
  problem: string | null;
  /** Absolute URLs (resolved against the API base). */
  videoUrl: string | null;
  thumbUrl: string | null;
  captionsUrl: string | null;
  product: ItemProduct | null;
  hostName: string | null;
}

export interface Playback {
  status: BroadcastStatus;
  playhead: Playhead | null;
  /** Where the show was at `serverTimeMs`. */
  position: Position | null;
  serverTimeMs: number;
  startedAt: string | null;
  endedAt: string | null;
}

export interface Broadcast {
  id: string;
  title: string;
  notes: string;
  loopMode: LoopMode;
  loopCount: number;
  intro: BroadcastItem | null;
  outro: BroadcastItem | null;
  items: BroadcastItem[];
  cycleS: number;
  totalS: number | null;
  playback: Playback;
  createdAt: string;
  updatedAt: string;
  /** Server clock minus this browser's clock, measured with this snapshot. */
  clockOffsetMs: number;
}

export interface BroadcastSummary {
  id: string;
  title: string;
  status: BroadcastStatus;
  loopMode: LoopMode;
  loopCount: number;
  itemCount: number;
  playableCount: number;
  cycleS: number;
  thumbUrl: string | null;
  nowPlaying: string | null;
  startedAt: string | null;
  createdAt: string;
}

export interface NewItem {
  role?: ItemRole;
  source: ItemSource;
  sourceRef: string;
  durationS?: number | null;
  label?: string;
  productId?: string | null;
}

export interface UploadedVideo {
  url: string;
  thumbUrl: string | null;
  durationS: number;
  width: number;
  height: number;
  name: string;
}
