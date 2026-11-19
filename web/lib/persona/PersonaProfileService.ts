// Web client for the persona profile surface. Mirrors the wire contract
// served by `soshop/api/routes/personas.py`. The Python handlers are
// the canonical reference, and any change here must move with them in
// lockstep.
//
// Endpoints:
//   GET  /personas/{id}             → profile (id, display_name, handle,
//                                     backstory, traits, avatar_url,
//                                     is_favorited)
//   GET  /personas/{id}/clips       → { clips: [...] } (always [] on the
//                                     v1 server, since the table does not
//                                     exist yet, but the web profile
//                                     renders gracefully against an empty
//                                     list)
//   POST /personas/{id}/favorite    { favorited: bool } → 204
//
// "Recent shows" in the web UI maps to the same `/clips` endpoint. We
// keep the wire field name `clips` to avoid contract drift, and only the
// rendering layer renames the section.
//
// Products: there is no `/personas/{id}/products` endpoint on the
// server today (search `soshop/api/routes/` — only the partner
// `/products` admin endpoint exists). The acceptance criteria call
// for a products list, so the service exposes a stub
// `fetchProducts` that returns an empty array. When the catalog
// endpoint lands, swap the implementation in place. Callers already
// render an empty state.

import { authedFetch, ApiError } from "@/lib/api";

// MARK: - Domain types

/**
 * Profile-shaped persona payload returned by `GET /personas/:id`.
 */
export interface PersonaProfile {
  id: string;
  displayName: string;
  handle: string;
  backstory: string;
  traits: string[];
  avatarUrl: string | null;
  isFavorited: boolean;
}

/**
 * One user-recorded clip from `GET /personas/:id/clips`. The web profile
 * renders these under the "Recent shows" heading. The wire field stays
 * `clips` to match the server.
 */
export interface PersonaClip {
  id: string;
  recordedAt: string;
  durationSeconds: number;
  transcript: string | null;
  audioUrl: string | null;
}

/**
 * Lightweight product card shown beneath the bio. The shape is a
 * forward-looking placeholder — the server has no
 * `/personas/{id}/products` endpoint yet, so the default service
 * returns `[]` and renderers degrade to an empty state. When the
 * catalog endpoint lands, the field set here matches the partner
 * `/products` payload in `soshop/api/routes/advertiser.py` so the
 * mapping is mechanical.
 */
export interface PersonaProduct {
  id: string;
  title: string;
  imageUrl: string | null;
  priceLabel: string | null;
}

// MARK: - Service interface

export interface PersonaProfileService {
  fetchProfile(personaId: string): Promise<PersonaProfile>;
  fetchClips(personaId: string): Promise<PersonaClip[]>;
  fetchProducts(personaId: string): Promise<PersonaProduct[]>;
  setFavorited(personaId: string, favorited: boolean): Promise<void>;
}

// MARK: - Wire DTOs

interface PersonaProfileDTO {
  id: string;
  display_name: string;
  handle: string;
  backstory: string;
  traits: string[];
  avatar_url: string | null;
  is_favorited: boolean;
}

interface ClipDTO {
  id: string;
  recorded_at: string;
  duration_seconds: number;
  transcript: string | null;
  audio_url: string | null;
}

interface ClipsEnvelopeDTO {
  clips: ClipDTO[];
}

function profileFromDto(dto: PersonaProfileDTO): PersonaProfile {
  return {
    id: dto.id,
    displayName: dto.display_name,
    handle: dto.handle,
    backstory: dto.backstory,
    traits: dto.traits,
    avatarUrl: dto.avatar_url,
    isFavorited: dto.is_favorited,
  };
}

function clipFromDto(dto: ClipDTO): PersonaClip {
  return {
    id: dto.id,
    recordedAt: dto.recorded_at,
    durationSeconds: dto.duration_seconds,
    transcript: dto.transcript,
    audioUrl: dto.audio_url,
  };
}

// MARK: - Default HTTP service

/**
 * HTTP-backed implementation. The backend is open access, so these
 * calls carry no auth token. Constructing the client at the call-site
 * keeps it decoupled from React hooks.
 */
export class DefaultPersonaProfileService implements PersonaProfileService {
  async fetchProfile(personaId: string): Promise<PersonaProfile> {
    const res = await authedFetch(`/personas/${encodeURIComponent(personaId)}`, {
      method: "GET",
    });
    const body = (await res.json()) as PersonaProfileDTO;
    return profileFromDto(body);
  }

  async fetchClips(personaId: string): Promise<PersonaClip[]> {
    const res = await authedFetch(`/personas/${encodeURIComponent(personaId)}/clips`, {
      method: "GET",
    });
    const body = (await res.json()) as ClipsEnvelopeDTO;
    return (body.clips ?? []).map(clipFromDto);
  }

  async fetchProducts(_personaId: string): Promise<PersonaProduct[]> {
    // No server endpoint yet (see header comment). Return an empty
    // list so callers can render the empty state without branching.
    // Swap to a real fetch when `/personas/{id}/products` lands.
    return [];
  }

  async setFavorited(personaId: string, favorited: boolean): Promise<void> {
    await authedFetch(`/personas/${encodeURIComponent(personaId)}/favorite`, {
      method: "POST",
      body: JSON.stringify({ favorited }),
    });
    // Cross-surface invalidation lives in TanStack Query: callers wrap
    // this call in `useMutation` and invalidate `favoritesKeys.list()`
    // in `onSettled`. See `components/persona/PersonaProfile.tsx`.
  }
}

// MARK: - Error helper

/**
 * Convenience: pull a user-facing message off whatever shape was thrown
 * inside an async load. The pattern is to always have a string to render
 * in the failure state.
 */
export function describeLoadError(error: unknown): string {
  if (error instanceof ApiError) return error.detail;
  if (error instanceof Error) return error.message;
  return "Failed to load persona profile";
}
