// Typed client for `/broadcasts/*`. The only module where the snake_case wire
// format is visible. Every broadcast it returns carries `clockOffsetMs`
// (server clock minus browser clock) so players can derive the on-air
// position locally.

import { ApiError, apiBaseUrl } from "@/lib/api";
import { errorMessageFromBody, request } from "@/lib/studio/api";
import { resolveMediaUrl } from "@/lib/studio/logic";
import type { Phase, Playhead, Position } from "./timeline";
import type {
  Broadcast,
  BroadcastItem,
  BroadcastStatus,
  BroadcastSummary,
  ControlAction,
  ItemRole,
  LoopMode,
  NewItem,
  UploadedVideo,
} from "./types";

// ---------------------------------------------------------------- wire types

interface WirePlayhead {
  phase: string;
  item_id: string;
  index?: number;
  cycle?: number;
  offset_ms: number;
  at_ms: number;
  playing: boolean;
}

interface WirePosition {
  phase: string;
  item_id: string;
  index: number;
  cycle: number;
  offset_ms: number;
  length_ms: number;
}

export interface WireItem {
  id: string;
  role: string;
  position: number;
  source: string;
  source_ref: string;
  label?: string | null;
  title: string;
  duration_s?: number | null;
  video_s?: number | null;
  slot_s?: number | null;
  playable: boolean;
  problem?: string | null;
  video_url?: string | null;
  thumb_url?: string | null;
  captions_url?: string | null;
  product?: {
    id: string;
    name: string;
    price?: number | null;
    currency?: string | null;
    image_url?: string | null;
  } | null;
  host_name?: string | null;
}

export interface WireBroadcast {
  id: string;
  title: string;
  notes?: string | null;
  loop_mode: string;
  loop_count: number;
  intro?: WireItem | null;
  outro?: WireItem | null;
  items: WireItem[];
  cycle_s: number;
  total_s?: number | null;
  playback: {
    status: string;
    playhead?: WirePlayhead | null;
    position?: WirePosition | null;
    server_time_ms: number;
    started_at?: string | null;
    ended_at?: string | null;
  };
  created_at: string;
  updated_at: string;
}

interface WireSummary {
  id: string;
  title: string;
  status: string;
  loop_mode: string;
  loop_count: number;
  item_count: number;
  playable_count: number;
  cycle_s: number;
  thumb_url?: string | null;
  now_playing?: string | null;
  started_at?: string | null;
  created_at: string;
}

interface WireUpload {
  url: string;
  thumb_url?: string | null;
  duration_s: number;
  width: number;
  height: number;
  name: string;
}

// ------------------------------------------------------------------ adapters

const STATUSES: BroadcastStatus[] = ["idle", "live", "paused", "ended"];
const ROLES: ItemRole[] = ["intro", "product", "outro"];
const PHASES: Phase[] = ["intro", "body", "outro"];

function oneOf<T extends string>(value: unknown, allowed: T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

const loopMode = (value: unknown): LoopMode => (value === "count" ? "count" : "continuous");
const media = (url: string | null | undefined) => resolveMediaUrl(url, apiBaseUrl());

export function adaptItem(wire: WireItem): BroadcastItem {
  return {
    id: wire.id,
    role: oneOf(wire.role, ROLES, "product"),
    position: wire.position,
    source: wire.source === "upload" ? "upload" : "promo",
    sourceRef: wire.source_ref,
    label: wire.label ?? "",
    title: wire.title,
    durationS: wire.duration_s ?? null,
    videoS: wire.video_s ?? null,
    slotS: wire.slot_s ?? null,
    playable: !!wire.playable,
    problem: wire.problem ?? null,
    videoUrl: media(wire.video_url),
    thumbUrl: media(wire.thumb_url),
    captionsUrl: media(wire.captions_url),
    product: wire.product
      ? {
          id: wire.product.id,
          name: wire.product.name,
          price: typeof wire.product.price === "number" ? wire.product.price : null,
          currency: (wire.product.currency ?? "VND").toUpperCase(),
          imageUrl: media(wire.product.image_url),
        }
      : null,
    hostName: wire.host_name ?? null,
  };
}

function adaptPlayhead(wire: WirePlayhead | null | undefined): Playhead | null {
  if (!wire) return null;
  return {
    phase: oneOf(wire.phase, PHASES, "body"),
    itemId: wire.item_id,
    index: wire.index ?? 0,
    cycle: wire.cycle ?? 0,
    offsetMs: wire.offset_ms,
    atMs: wire.at_ms,
    playing: !!wire.playing,
  };
}

function adaptPosition(wire: WirePosition | null | undefined): Position | null {
  if (!wire) return null;
  return {
    phase: oneOf(wire.phase, PHASES, "body"),
    itemId: wire.item_id,
    index: wire.index,
    cycle: wire.cycle,
    offsetMs: wire.offset_ms,
    lengthMs: wire.length_ms,
  };
}

export function adaptBroadcast(wire: WireBroadcast, clockOffsetMs = 0): Broadcast {
  return {
    id: wire.id,
    title: wire.title,
    notes: wire.notes ?? "",
    loopMode: loopMode(wire.loop_mode),
    loopCount: wire.loop_count,
    intro: wire.intro ? adaptItem(wire.intro) : null,
    outro: wire.outro ? adaptItem(wire.outro) : null,
    items: wire.items.map(adaptItem),
    cycleS: wire.cycle_s,
    totalS: wire.total_s ?? null,
    playback: {
      status: oneOf(wire.playback.status, STATUSES, "idle"),
      playhead: adaptPlayhead(wire.playback.playhead),
      position: adaptPosition(wire.playback.position),
      serverTimeMs: wire.playback.server_time_ms,
      startedAt: wire.playback.started_at ?? null,
      endedAt: wire.playback.ended_at ?? null,
    },
    createdAt: wire.created_at,
    updatedAt: wire.updated_at,
    clockOffsetMs,
  };
}

export function adaptSummary(wire: WireSummary): BroadcastSummary {
  return {
    id: wire.id,
    title: wire.title,
    status: oneOf(wire.status, STATUSES, "idle"),
    loopMode: loopMode(wire.loop_mode),
    loopCount: wire.loop_count,
    itemCount: wire.item_count,
    playableCount: wire.playable_count,
    cycleS: wire.cycle_s,
    thumbUrl: media(wire.thumb_url),
    nowPlaying: wire.now_playing ?? null,
    startedAt: wire.started_at ?? null,
    createdAt: wire.created_at,
  };
}

/** Server minus browser clock, assuming the reply was stamped mid-flight. */
export function clockOffset(serverMs: number, sentMs: number, receivedMs: number): number {
  return Math.round(serverMs - (sentMs + receivedMs) / 2);
}

/** Parse one `/events` message; `null` if it is not a broadcast. */
export function parseBroadcastEvent(data: string, receivedMs: number): Broadcast | null {
  try {
    const wire = JSON.parse(data) as WireBroadcast;
    if (!wire || typeof wire.id !== "string" || !wire.playback) return null;
    return adaptBroadcast(wire, Math.round(wire.playback.server_time_ms - receivedMs));
  } catch {
    return null;
  }
}

/** The newer of two snapshots of the same broadcast (by server time). */
export function newerBroadcast(prev: Broadcast | undefined, next: Broadcast): Broadcast {
  if (prev && prev.id === next.id && prev.playback.serverTimeMs > next.playback.serverTimeMs) {
    return prev;
  }
  return next;
}

// ------------------------------------------------------------------- calls

const enc = encodeURIComponent;
const base = (id: string) => `/broadcasts/${enc(id)}`;

async function call(method: string, path: string, body?: { json: unknown }): Promise<Broadcast> {
  const sent = Date.now();
  const wire = await request<WireBroadcast>(method, path, body);
  return adaptBroadcast(wire, clockOffset(wire.playback.server_time_ms, sent, Date.now()));
}

export async function listBroadcasts(): Promise<BroadcastSummary[]> {
  const data = await request<{ broadcasts: WireSummary[] }>("GET", "/broadcasts");
  return data.broadcasts.map(adaptSummary);
}

export function getBroadcast(id: string): Promise<Broadcast> {
  return call("GET", base(id));
}

export function createBroadcast(input: {
  title: string;
  loopMode?: LoopMode;
  loopCount?: number;
}): Promise<Broadcast> {
  return call("POST", "/broadcasts", {
    json: { title: input.title.trim(), loop_mode: input.loopMode, loop_count: input.loopCount },
  });
}

export function updateBroadcast(
  id: string,
  input: { title?: string; notes?: string; loopMode?: LoopMode; loopCount?: number },
): Promise<Broadcast> {
  const json: Record<string, unknown> = {};
  if (input.title !== undefined) json.title = input.title.trim();
  if (input.notes !== undefined) json.notes = input.notes;
  if (input.loopMode !== undefined) json.loop_mode = input.loopMode;
  if (input.loopCount !== undefined) json.loop_count = input.loopCount;
  return call("PATCH", base(id), { json });
}

export async function deleteBroadcast(id: string): Promise<void> {
  await request<void>("DELETE", base(id));
}

export function addItems(id: string, items: NewItem[]): Promise<Broadcast> {
  return call("POST", `${base(id)}/items`, {
    json: {
      items: items.map((item) => ({
        role: item.role ?? "product",
        source: item.source,
        source_ref: item.sourceRef,
        duration_s: item.durationS ?? null,
        label: item.label ?? "",
        product_id: item.productId ?? null,
      })),
    },
  });
}

export function updateItem(
  id: string,
  itemId: string,
  input: { durationS?: number | null; label?: string },
): Promise<Broadcast> {
  const json: Record<string, unknown> = {};
  if (input.durationS !== undefined) json.duration_s = input.durationS;
  if (input.label !== undefined) json.label = input.label;
  return call("PATCH", `${base(id)}/items/${enc(itemId)}`, { json });
}

export function removeItem(id: string, itemId: string): Promise<Broadcast> {
  return call("DELETE", `${base(id)}/items/${enc(itemId)}`);
}

export function reorderItems(id: string, itemIds: string[]): Promise<Broadcast> {
  return call("PUT", `${base(id)}/items/order`, { json: { item_ids: itemIds } });
}

export function controlBroadcast(
  id: string,
  action: ControlAction,
  itemId?: string,
): Promise<Broadcast> {
  return call("POST", `${base(id)}/control`, { json: { action, item_id: itemId ?? null } });
}

export function eventsUrl(id: string): string {
  return `${apiBaseUrl()}${base(id)}/events`;
}

/** Upload an intro, outro or product video, reporting progress (0 to 1). */
export function uploadVideo(
  file: File,
  onProgress?: (fraction: number) => void,
): Promise<UploadedVideo> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${apiBaseUrl()}/broadcasts/uploads`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable) onProgress?.(event.loaded / event.total);
    };
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        // Non-JSON body: fall back to the status text below.
      }
      if (xhr.status >= 200 && xhr.status < 300 && body) {
        const wire = body as WireUpload;
        resolve({
          url: wire.url,
          thumbUrl: media(wire.thumb_url),
          durationS: wire.duration_s,
          width: wire.width,
          height: wire.height,
          name: wire.name,
        });
        return;
      }
      const fallback = xhr.statusText || `HTTP ${xhr.status}`;
      reject(new ApiError(xhr.status, errorMessageFromBody(body, fallback)));
    };
    xhr.onerror = () =>
      reject(new ApiError(0, "Không kết nối được máy chủ API. Kiểm tra server đã chạy chưa."));
    const form = new FormData();
    form.set("file", file);
    xhr.send(form);
  });
}
