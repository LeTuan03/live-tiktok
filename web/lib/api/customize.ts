// Client for the public `/customize` ("create your AI host") endpoints.
//
// Mirrors the legacy hand-mirrored clients (snake_case wire -> camelCase
// domain via small adapters). The voices GET goes through `authedFetch`; the
// host POST is multipart, so it bypasses `authedFetch` (which would force
// `Content-Type: application/json`) and lets the browser set the boundary.

import { ApiError, apiBaseUrl, authedFetch } from "@/lib/api";

export interface CustomizeVoice {
  voiceId: string;
  // The voice's owner in the public library. Echoed back on submit so the
  // server can copy the voice into our account and guarantee it stays usable.
  publicOwnerId: string | null;
  name: string;
  previewUrl: string | null;
  category: string | null;
  gender: string | null;
  age: string | null;
  accent: string | null;
  language: string | null;
  useCase: string | null;
  descriptive: string | null;
  imageUrl: string | null;
}

interface VoiceWire {
  voice_id: string;
  public_owner_id: string | null;
  name: string;
  preview_url: string | null;
  category: string | null;
  gender: string | null;
  age: string | null;
  accent: string | null;
  language: string | null;
  use_case: string | null;
  descriptive: string | null;
  image_url: string | null;
}

function adaptVoice(wire: VoiceWire): CustomizeVoice {
  return {
    voiceId: wire.voice_id,
    publicOwnerId: wire.public_owner_id,
    name: wire.name,
    previewUrl: wire.preview_url,
    category: wire.category,
    gender: wire.gender,
    age: wire.age,
    accent: wire.accent,
    language: wire.language,
    useCase: wire.use_case,
    descriptive: wire.descriptive,
    imageUrl: wire.image_url,
  };
}

/**
 * Fetch the server's curated shortlist of voices (8: 4 male then 4 female).
 * No query params and no pagination: the server returns the full list.
 */
export async function listVoices(signal?: AbortSignal): Promise<CustomizeVoice[]> {
  const res = await authedFetch("/customize/voices", { signal });
  const data = (await res.json()) as { voices: VoiceWire[] };
  return data.voices.map(adaptVoice);
}

export interface CustomHostInput {
  avatar: File;
  voiceId: string;
  publicOwnerId?: string | null;
  personality: string;
  script: string;
  name?: string;
}

export interface CustomHostSession {
  roomName: string;
  livekitUrl: string;
  token: string;
  personaId: string;
  sessionId: string;
}

interface HostWire {
  room_name: string;
  livekit_url: string;
  token: string;
  persona_id: string;
  session_id: string;
}

/** Read the canonical error envelope (or legacy `detail`) into an `ApiError`. */
async function readError(res: Response): Promise<ApiError> {
  let detail = res.statusText || `HTTP ${res.status}`;
  try {
    const data = (await res.json()) as {
      error?: { message?: unknown; code?: unknown };
      detail?: unknown;
    };
    if (data?.error && typeof data.error.message === "string") {
      detail = data.error.message;
    } else if (typeof data?.detail === "string") {
      detail = data.detail;
    }
  } catch {
    // Non-JSON body; keep statusText.
  }
  return new ApiError(res.status, detail);
}

/** Create an ephemeral AI host and dispatch it into a live room. */
export async function createCustomHost(
  input: CustomHostInput,
  signal?: AbortSignal,
): Promise<CustomHostSession> {
  const form = new FormData();
  form.append("avatar", input.avatar);
  form.append("voice_id", input.voiceId);
  if (input.publicOwnerId) form.append("public_owner_id", input.publicOwnerId);
  form.append("personality", input.personality);
  form.append("script", input.script);
  if (input.name?.trim()) form.append("name", input.name.trim());

  const res = await fetch(`${apiBaseUrl()}/customize/host`, {
    method: "POST",
    body: form,
    signal,
    cache: "no-store",
  });
  if (!res.ok) throw await readError(res);
  const data = (await res.json()) as HostWire;
  return {
    roomName: data.room_name,
    livekitUrl: data.livekit_url,
    token: data.token,
    personaId: data.persona_id,
    sessionId: data.session_id,
  };
}
