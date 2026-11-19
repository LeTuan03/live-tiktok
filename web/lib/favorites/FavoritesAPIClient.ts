// Web favorites client. The server-side route is the source of truth, so
// changes here must move in lockstep with the Python handler in
// `soshop/api/routes/personas.py`.
//
// Endpoints (server: `soshop/api/routes/personas.py`):
//   GET  /favorites?cursor=&limit=     → { favorites: [...], next_cursor }
//   POST /favorites/{persona_id}/pin   { pinned: boolean }
//
// The web surface uses infinite scroll, so it passes `cursor` / `limit`
// and reads `next_cursor` from the envelope. When the server returns no
// cursor (single-page response), the list just stops paginating.
//
// Cache invalidation lives in TanStack Query: any surface that mutates
// favorites (persona profile, feed card heart) issues a
// `useMutation` whose `onSettled` calls
// `queryClient.invalidateQueries({ queryKey: favoritesKeys.list() })`.
// See `lib/favorites/queryKeys.ts`.

import { authedFetch } from "@/lib/api";

// MARK: - Domain types

/**
 * One row in the user's favorites list. Lighter than a full persona
 * profile, only enough to render the row and route to the profile on
 * tap.
 */
export interface FavoriteEntry {
  id: string;
  displayName: string;
  handle: string;
  avatarUrl: string | null;
  isPinned: boolean;
  /**
   * Server-supplied pin timestamp (ISO 8601). Used to break ties when
   * more than one favorite is pinned — most-recently-pinned floats to
   * the top of the pinned section. `null` for unpinned rows.
   */
  pinnedAt: string | null;
}

export interface FavoritesPage {
  favorites: FavoriteEntry[];
  /** Opaque cursor for the next page; `null` when there are no more rows. */
  nextCursor: string | null;
}

// MARK: - API client

export interface FavoritesAPIClient {
  fetchFavorites(args?: { cursor?: string | null; limit?: number }): Promise<FavoritesPage>;
  setPinned(personaId: string, pinned: boolean): Promise<void>;
}

// MARK: - Wire DTOs

interface FavoriteDTO {
  id: string;
  display_name: string;
  handle: string;
  avatar_url: string | null;
  is_pinned: boolean;
  pinned_at: string | null;
}

interface FavoritesEnvelopeDTO {
  favorites: FavoriteDTO[];
  next_cursor?: string | null;
}

function dtoToEntry(dto: FavoriteDTO): FavoriteEntry {
  return {
    id: dto.id,
    displayName: dto.display_name,
    handle: dto.handle,
    avatarUrl: dto.avatar_url ?? null,
    isPinned: dto.is_pinned,
    pinnedAt: dto.pinned_at ?? null,
  };
}

// MARK: - Default HTTP client

/**
 * Default HTTP-backed implementation. The backend is open access, so
 * these calls carry no auth token; the client can be constructed at
 * module scope and stays decoupled from React hooks.
 */
export class DefaultFavoritesAPIClient implements FavoritesAPIClient {
  async fetchFavorites({
    cursor,
    limit = 30,
  }: {
    cursor?: string | null;
    limit?: number;
  } = {}): Promise<FavoritesPage> {
    const params = new URLSearchParams();
    if (cursor) params.set("cursor", cursor);
    params.set("limit", String(limit));

    const res = await authedFetch(`/favorites?${params.toString()}`, {
      method: "GET",
    });
    const body = (await res.json()) as FavoritesEnvelopeDTO;
    return {
      favorites: (body.favorites ?? []).map(dtoToEntry),
      nextCursor: body.next_cursor ?? null,
    };
  }

  async setPinned(personaId: string, pinned: boolean): Promise<void> {
    await authedFetch(`/favorites/${encodeURIComponent(personaId)}/pin`, {
      method: "POST",
      body: JSON.stringify({ pinned }),
    });
  }
}

// MARK: - Sort

/**
 * Pinned section first (most-recent pin floats to the top), then
 * unpinned rows alphabetized by `displayName`. Matches the server's
 * canonical sort so the optimistic reorder lines up with the backend
 * response on the next reload.
 */
export function sortFavorites(entries: FavoriteEntry[]): FavoriteEntry[] {
  return [...entries].sort((lhs, rhs) => {
    if (lhs.isPinned !== rhs.isPinned) {
      return lhs.isPinned ? -1 : 1;
    }
    if (lhs.isPinned && rhs.isPinned) {
      const lhsAt = lhs.pinnedAt ? Date.parse(lhs.pinnedAt) : -Infinity;
      const rhsAt = rhs.pinnedAt ? Date.parse(rhs.pinnedAt) : -Infinity;
      if (lhsAt !== rhsAt) return rhsAt - lhsAt;
    }
    return lhs.displayName.localeCompare(rhs.displayName, undefined, {
      sensitivity: "base",
    });
  });
}
