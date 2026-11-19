import { ApiError, authedFetch } from "@/lib/api";

/**
 * Client for the consumer persona/AskAvatar API. Provisions a
 * server-issued session that mints a LiveKit token and dispatches the
 * SoShop agent into a dedicated 1-to-1 room.
 *
 * Errors flow through the canonical `ApiError` from
 * `@/components/lib/api`; `PersonaApiError` is a thin alias kept for
 * call-site compatibility.
 */

export class PersonaApiError extends ApiError {
  constructor(status: number, detail: string, code?: string) {
    super(status, detail, { code });
    this.name = "PersonaApiError";
  }
}

export interface FirstPersonaSpec {
  persona_id: string;
  script_id: string;
  product_id?: string | null;
  pitch_window_ms?: number;
}

export interface CreatePersonaSessionRequest {
  first_persona: FirstPersonaSpec;
  device_id?: string | null;
  app_version?: string | null;
  country?: string | null;
}

export interface PersonaSessionCreated {
  session_id: string;
  avatar_mode: "avatar" | "non_avatar";
  minutes_used: number;
  minutes_limit: number;
  seconds_until_reset: number;
  room_name: string | null;
  livekit_url: string | null;
  livekit_token: string | null;
  persona_id: string | null;
  dispatch_id: string | null;
  swap_mode: "single_room" | "ping_pong" | null;
}

/**
 * Create a 1-to-1 persona session and obtain a LiveKit join token.
 *
 * The server enforces both the global concurrent-room cap (returns 503
 * with `Retry-After`) and the per-user daily LemonSlice-minute cap
 * (returns 200 with `avatar_mode: "non_avatar"`). Callers should branch
 * on the response's `avatar_mode` rather than treating non-avatar as an
 * error.
 */
export async function createPersonaSession(
  token: string,
  body: CreatePersonaSessionRequest,
): Promise<PersonaSessionCreated> {
  const res = await authedFetch("/feed/session", {
    method: "POST",
    body: JSON.stringify(body),
    token,
  });
  return res.json() as Promise<PersonaSessionCreated>;
}
