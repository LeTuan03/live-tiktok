// Typed client for the `/studio/*` API. This is the only module where the
// snake_case wire format is visible; everything it returns is camelCase.
//
// Errors: the server wraps every non-2xx as
// `{ "error": { "code", "message", "fields"? } }`. We surface `error.message`
// through the shared `ApiError`, falling back to the legacy FastAPI `detail`
// string and then to the HTTP status text.

import { ApiError, apiBaseUrl } from "@/lib/api";
import type {
  AvatarStatus,
  ControlAction,
  LiveConnection,
  LiveMode,
  LiveState,
  ModelCreateInput,
  ProductInput,
  ProductStatus,
  PublicProduct,
  PublicSession,
  SessionStatus,
  StudioLanguage,
  StudioModel,
  StudioProduct,
  StudioSession,
  StudioVoice,
} from "./types";

// ---------------------------------------------------------------- wire types

export interface WireModel {
  id: string;
  name: string;
  intro?: string | null;
  personality?: string | null;
  language?: string | null;
  voice?: string | null;
  image_url?: string | null;
  avatar_status?: string | null;
  avatar_progress?: number | null;
  avatar_error?: string | null;
  created_at?: string | null;
}

export interface WireProduct {
  id: string;
  session_id: string;
  position: number;
  name: string;
  price?: number | string | null;
  currency?: string | null;
  description?: string | null;
  highlights?: string | null;
  image_url?: string | null;
  script?: string | null;
  status?: string | null;
}

export interface WireLiveState {
  mode?: string | null;
  current_product_id?: string | null;
  last_line?: string | null;
  updated_at?: string | null;
}

export interface WireSession {
  id: string;
  title: string;
  model_id?: string | null;
  model?: WireModel | null;
  notes?: string | null;
  status?: string | null;
  room_name?: string | null;
  started_at?: string | null;
  ended_at?: string | null;
  created_at?: string | null;
  live?: WireLiveState | null;
  products?: WireProduct[] | null;
}

interface WireConnection {
  livekit_url: string;
  token: string;
  room_name: string;
}

interface WirePublicSession {
  id: string;
  title: string;
  status?: string | null;
  model?: { name: string; image_url?: string | null } | null;
  live?: WireLiveState | null;
  products?: Array<Omit<WireProduct, "session_id" | "highlights" | "script">> | null;
}

// ------------------------------------------------------------------ adapters

const AVATAR_STATUSES: AvatarStatus[] = ["none", "processing", "ready", "failed"];
const PRODUCT_STATUSES: ProductStatus[] = ["pending", "presenting", "done", "skipped"];
const SESSION_STATUSES: SessionStatus[] = ["draft", "live", "ended"];
const LIVE_MODES: LiveMode[] = ["starting", "chatting", "presenting", "paused", "ending"];

function oneOf<T extends string>(value: unknown, allowed: T[], fallback: T): T {
  return allowed.includes(value as T) ? (value as T) : fallback;
}

function toPrice(value: number | string | null | undefined): number | null {
  if (value === null || value === undefined || value === "") return null;
  const n = typeof value === "number" ? value : Number(value);
  return Number.isFinite(n) ? n : null;
}

export function adaptModel(wire: WireModel): StudioModel {
  return {
    id: wire.id,
    name: wire.name,
    intro: wire.intro ?? "",
    personality: wire.personality ?? "",
    language: (wire.language === "en" ? "en" : "vi") as StudioLanguage,
    voice: wire.voice ?? "",
    imageUrl: wire.image_url ?? null,
    avatarStatus: oneOf(wire.avatar_status, AVATAR_STATUSES, "none"),
    avatarProgress: typeof wire.avatar_progress === "number" ? wire.avatar_progress : null,
    avatarError: wire.avatar_error ?? null,
    createdAt: wire.created_at ?? null,
  };
}

export function adaptProduct(wire: WireProduct): StudioProduct {
  return {
    id: wire.id,
    sessionId: wire.session_id,
    position: wire.position,
    name: wire.name,
    price: toPrice(wire.price),
    currency: wire.currency || "VND",
    description: wire.description ?? "",
    highlights: wire.highlights ?? "",
    imageUrl: wire.image_url ?? null,
    script: wire.script ?? "",
    status: oneOf(wire.status, PRODUCT_STATUSES, "pending"),
  };
}

export function adaptLiveState(wire: WireLiveState | null | undefined): LiveState | null {
  if (!wire || typeof wire !== "object") return null;
  if (!LIVE_MODES.includes(wire.mode as LiveMode)) return null;
  return {
    mode: wire.mode as LiveMode,
    currentProductId: wire.current_product_id ?? null,
    lastLine: wire.last_line ?? null,
    updatedAt: wire.updated_at ?? null,
  };
}

function byPosition<T extends { position: number }>(a: T, b: T): number {
  return a.position - b.position;
}

export function adaptSession(wire: WireSession): StudioSession {
  return {
    id: wire.id,
    title: wire.title,
    modelId: wire.model_id ?? null,
    model: wire.model ? adaptModel(wire.model) : null,
    notes: wire.notes ?? "",
    status: oneOf(wire.status, SESSION_STATUSES, "draft"),
    roomName: wire.room_name ?? null,
    startedAt: wire.started_at ?? null,
    endedAt: wire.ended_at ?? null,
    createdAt: wire.created_at ?? null,
    live: adaptLiveState(wire.live),
    products: (wire.products ?? []).map(adaptProduct).sort(byPosition),
  };
}

function adaptConnection(wire: WireConnection): LiveConnection {
  return { livekitUrl: wire.livekit_url, token: wire.token, roomName: wire.room_name };
}

export function adaptPublicSession(wire: WirePublicSession): PublicSession {
  const products: PublicProduct[] = (wire.products ?? [])
    .map((p) => ({
      id: p.id,
      position: p.position,
      name: p.name,
      price: toPrice(p.price),
      currency: p.currency || "VND",
      description: p.description ?? "",
      imageUrl: p.image_url ?? null,
      status: oneOf(p.status, PRODUCT_STATUSES, "pending"),
    }))
    .sort(byPosition);
  return {
    id: wire.id,
    title: wire.title,
    status: oneOf(wire.status, SESSION_STATUSES, "draft"),
    model: wire.model ? { name: wire.model.name, imageUrl: wire.model.image_url ?? null } : null,
    live: adaptLiveState(wire.live),
    products,
  };
}

/** Product fields as the server expects them. Only keys present in `input`
 *  are emitted, so the same helper serves create and partial update. */
export function productToWire(input: Partial<ProductInput>): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  if (input.name !== undefined) out.name = input.name;
  if (input.price !== undefined) out.price = input.price;
  if (input.currency !== undefined) out.currency = input.currency;
  if (input.description !== undefined) out.description = input.description;
  if (input.highlights !== undefined) out.highlights = input.highlights;
  if (input.imageUrl !== undefined) out.image_url = input.imageUrl;
  if (input.script !== undefined) out.script = input.script;
  return out;
}

// ------------------------------------------------------------------ transport

interface ErrorBody {
  error?: { code?: unknown; message?: unknown; fields?: unknown } | null;
  detail?: unknown;
}

/** Pull a human-readable message out of an error response body. */
export function errorMessageFromBody(body: unknown, fallback: string): string {
  if (!body || typeof body !== "object") return fallback;
  const data = body as ErrorBody;
  if (data.error && typeof data.error === "object") {
    const fields = data.error.fields;
    if (Array.isArray(fields) && fields.length > 0) {
      const first = fields[0] as { msg?: unknown };
      if (typeof data.error.message === "string" && typeof first?.msg === "string") {
        return `${data.error.message}: ${first.msg}`;
      }
    }
    if (typeof data.error.message === "string" && data.error.message) return data.error.message;
    if (typeof data.error.code === "string" && data.error.code) return data.error.code;
  }
  if (typeof data.detail === "string" && data.detail) return data.detail;
  if (Array.isArray(data.detail) && data.detail.length > 0) {
    const first = data.detail[0] as { msg?: unknown };
    if (typeof first?.msg === "string") return first.msg;
  }
  return fallback;
}

async function toApiError(res: Response): Promise<ApiError> {
  const fallback = res.statusText || `HTTP ${res.status}`;
  let message = fallback;
  let code: string | undefined;
  try {
    const body = (await res.json()) as ErrorBody;
    message = errorMessageFromBody(body, fallback);
    if (body?.error && typeof body.error.code === "string") code = body.error.code;
  } catch {
    // Non-JSON body: keep the status text.
  }
  return new ApiError(res.status, message, { code });
}

export type RequestBody = { json: unknown } | { form: FormData } | undefined;

/** Shared fetch wrapper: JSON or multipart body, `ApiError` on any non-2xx. */
export async function request<T>(method: string, path: string, body?: RequestBody): Promise<T> {
  const init: RequestInit = { method, cache: "no-store" };
  if (body && "json" in body) {
    init.headers = { "Content-Type": "application/json" };
    init.body = JSON.stringify(body.json);
  } else if (body && "form" in body) {
    // No Content-Type: the browser sets the multipart boundary itself.
    init.body = body.form;
  }
  let res: Response;
  try {
    res = await fetch(`${apiBaseUrl()}${path}`, init);
  } catch {
    throw new ApiError(0, "Không kết nối được máy chủ API. Kiểm tra server đã chạy chưa.");
  }
  if (!res.ok) throw await toApiError(res);
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

/** Message to show for any thrown value from this client. */
export function describeError(err: unknown): string {
  if (err instanceof ApiError) return err.detail;
  if (err instanceof Error && err.message) return err.message;
  return "Đã xảy ra lỗi không xác định.";
}

const enc = encodeURIComponent;

// --------------------------------------------------------------------- models

export async function listVoices(): Promise<StudioVoice[]> {
  const data = await request<{ voices: StudioVoice[] }>("GET", "/studio/voices");
  return data.voices ?? [];
}

export async function listModels(): Promise<StudioModel[]> {
  const data = await request<{ models: WireModel[] }>("GET", "/studio/models");
  return (data.models ?? []).map(adaptModel);
}

export async function createModel(input: ModelCreateInput): Promise<StudioModel> {
  const form = new FormData();
  form.set("name", input.name);
  if (input.intro) form.set("intro", input.intro);
  if (input.personality) form.set("personality", input.personality);
  form.set("language", input.language);
  if (input.voice) form.set("voice", input.voice);
  form.set("image", input.image);
  return adaptModel(await request<WireModel>("POST", "/studio/models", { form }));
}

export async function updateModel(
  id: string,
  patch: Partial<Pick<StudioModel, "name" | "intro" | "personality" | "language" | "voice">>,
): Promise<StudioModel> {
  return adaptModel(
    await request<WireModel>("PATCH", `/studio/models/${enc(id)}`, { json: patch }),
  );
}

export async function deleteModel(id: string): Promise<void> {
  await request<void>("DELETE", `/studio/models/${enc(id)}`);
}

export async function buildModelAvatar(id: string): Promise<StudioModel> {
  return adaptModel(await request<WireModel>("POST", `/studio/models/${enc(id)}/avatar`));
}

// ------------------------------------------------------------------- sessions

export async function listSessions(): Promise<StudioSession[]> {
  const data = await request<{ sessions: WireSession[] }>("GET", "/studio/sessions");
  return (data.sessions ?? []).map(adaptSession);
}

export async function createSession(input: {
  title: string;
  modelId?: string | null;
}): Promise<StudioSession> {
  const json: Record<string, unknown> = { title: input.title };
  if (input.modelId) json.model_id = input.modelId;
  return adaptSession(await request<WireSession>("POST", "/studio/sessions", { json }));
}

export async function getSession(id: string): Promise<StudioSession> {
  return adaptSession(await request<WireSession>("GET", `/studio/sessions/${enc(id)}`));
}

export async function updateSession(
  id: string,
  patch: { title?: string; modelId?: string | null; notes?: string },
): Promise<StudioSession> {
  const json: Record<string, unknown> = {};
  if (patch.title !== undefined) json.title = patch.title;
  if (patch.modelId !== undefined) json.model_id = patch.modelId;
  if (patch.notes !== undefined) json.notes = patch.notes;
  return adaptSession(await request<WireSession>("PATCH", `/studio/sessions/${enc(id)}`, { json }));
}

export async function deleteSession(id: string): Promise<void> {
  await request<void>("DELETE", `/studio/sessions/${enc(id)}`);
}

// ------------------------------------------------------------------- products

export async function createProduct(
  sessionId: string,
  input: Partial<ProductInput> & { name: string },
): Promise<StudioProduct> {
  return adaptProduct(
    await request<WireProduct>("POST", `/studio/sessions/${enc(sessionId)}/products`, {
      json: productToWire(input),
    }),
  );
}

export async function updateProduct(
  productId: string,
  patch: Partial<ProductInput>,
): Promise<StudioProduct> {
  return adaptProduct(
    await request<WireProduct>("PATCH", `/studio/products/${enc(productId)}`, {
      json: productToWire(patch),
    }),
  );
}

export async function deleteProduct(productId: string): Promise<void> {
  await request<void>("DELETE", `/studio/products/${enc(productId)}`);
}

export async function reorderProducts(
  sessionId: string,
  productIds: string[],
): Promise<StudioProduct[]> {
  const data = await request<{ products: WireProduct[] }>(
    "PUT",
    `/studio/sessions/${enc(sessionId)}/products/order`,
    { json: { product_ids: productIds } },
  );
  return (data.products ?? []).map(adaptProduct).sort(byPosition);
}

export async function generateProductScript(productId: string): Promise<StudioProduct> {
  return adaptProduct(
    await request<WireProduct>("POST", `/studio/products/${enc(productId)}/script`),
  );
}

export async function uploadImage(file: File): Promise<string> {
  const form = new FormData();
  form.set("file", file);
  const data = await request<{ url: string }>("POST", "/studio/uploads", { form });
  return data.url;
}

// ----------------------------------------------------------------------- live

export async function startSession(
  id: string,
): Promise<{ connection: LiveConnection; session: StudioSession }> {
  const data = await request<WireConnection & { session: WireSession }>(
    "POST",
    `/studio/sessions/${enc(id)}/start`,
  );
  return { connection: adaptConnection(data), session: adaptSession(data.session) };
}

export async function endSession(id: string): Promise<StudioSession> {
  return adaptSession(await request<WireSession>("POST", `/studio/sessions/${enc(id)}/end`));
}

export async function sendControl(
  id: string,
  action: ControlAction,
  extra: { productId?: string; text?: string } = {},
): Promise<void> {
  const json: Record<string, unknown> = { action };
  if (extra.productId) json.product_id = extra.productId;
  if (extra.text) json.text = extra.text;
  await request<{ ok: boolean }>("POST", `/studio/sessions/${enc(id)}/control`, { json });
}

export async function getLiveToken(
  id: string,
  role: "host" | "viewer",
  name?: string,
): Promise<LiveConnection> {
  const query = new URLSearchParams({ role });
  if (name) query.set("name", name);
  return adaptConnection(
    await request<WireConnection>("GET", `/studio/sessions/${enc(id)}/token?${query.toString()}`),
  );
}

export async function getPublicSession(id: string): Promise<PublicSession> {
  return adaptPublicSession(
    await request<WirePublicSession>("GET", `/studio/sessions/${enc(id)}/public`),
  );
}
