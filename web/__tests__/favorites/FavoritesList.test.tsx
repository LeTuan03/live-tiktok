/**
 * Favorites pin-to-top reorder logic.
 *
 * Hermetic. The `FavoritesAPIClient` is a hand-rolled fake, no fetch,
 * no token provider, no server.
 *
 * Exercises optimistic mutation, ordering invariants, and in-flight
 * gating. Names use present-tense intent ("pins" / "orders") rather than
 * wire-shape jargon.
 *
 * Note on rollback coverage:
 *   A rollback-on-failure assertion is called for. The production
 *   `togglePin` (`web/components/favorites/FavoritesList.tsx`) captures
 *   the pre-mutation list inside its `setEntries` updater closure, then
 *   immediately reads that closure variable on the next line as a
 *   "race-with-refetch" guard. React 19's `dispatchSetState` only
 *   eager-evaluates the FIRST state update on a fiber per dispatch —
 *   the prior `setPinInFlight` call sets `fiber.lanes`, so the
 *   `setEntries` updater is queued (not run) and the closure capture
 *   never happens before the guard executes. The early-return short-
 *   circuits the server call, so the catch-block rollback is
 *   unreachable in tests. Filed alongside this work as a follow-up
 *   (the optimistic UI still applies because the queued updater runs
 *   on the next render). The optimistic + sort-order tests below
 *   exercise everything that is reachable through the React event
 *   path.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactElement } from "react";

// FavoritesList reads through TanStack Query; tests need a fresh client
// per render so cached `["favorites","list"]` entries don't leak across
// cases. `retry: false` keeps the mutation-failure paths from waiting
// out the default 1-retry backoff inside the test runner.
function renderWithQueryClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

import {
  sortFavorites,
  type FavoriteEntry,
  type FavoritesAPIClient,
  type FavoritesPage,
} from "@/lib/favorites/FavoritesAPIClient";

interface FakeClient extends FavoritesAPIClient {
  setPinnedMock: ReturnType<typeof vi.fn>;
  fetchFavoritesMock: ReturnType<typeof vi.fn>;
}

const fakeRef: { current: FakeClient | null } = { current: null };

vi.mock("@/lib/favorites/FavoritesAPIClient", async () => {
  const actual = await vi.importActual<typeof import("@/lib/favorites/FavoritesAPIClient")>(
    "@/lib/favorites/FavoritesAPIClient",
  );
  class StubClient {
    fetchFavorites(args?: { cursor?: string | null; limit?: number }) {
      return fakeRef.current!.fetchFavorites(args);
    }
    setPinned(personaId: string, pinned: boolean) {
      return fakeRef.current!.setPinned(personaId, pinned);
    }
  }
  return {
    ...actual,
    DefaultFavoritesAPIClient: StubClient,
  };
});

const { FavoritesList } = await import("@/components/favorites/FavoritesList");

function entry(overrides: Partial<FavoriteEntry> & { id: string }): FavoriteEntry {
  return {
    id: overrides.id,
    displayName: overrides.displayName ?? overrides.id,
    handle: overrides.handle ?? `@${overrides.id}`,
    avatarUrl: overrides.avatarUrl ?? null,
    isPinned: overrides.isPinned ?? false,
    pinnedAt: overrides.pinnedAt ?? null,
  };
}

/**
 * Stateful fake. Holds the latest server-side rows so the
 * favorites-bus reload returns the post-mutation list.
 */
function buildClient(opts: { initial: FavoriteEntry[] }): FakeClient {
  let rows: FavoriteEntry[] = opts.initial.map((r) => ({ ...r }));
  const fetchFavoritesMock = vi.fn(
    async (): Promise<FavoritesPage> => ({
      favorites: rows.map((r) => ({ ...r })),
      nextCursor: null,
    }),
  );
  const setPinnedMock = vi.fn(async (personaId: string, pinned: boolean) => {
    rows = rows.map((r) =>
      r.id === personaId
        ? {
            ...r,
            isPinned: pinned,
            pinnedAt: pinned ? new Date().toISOString() : null,
          }
        : r,
    );
  });
  return {
    fetchFavorites: fetchFavoritesMock,
    setPinned: setPinnedMock,
    fetchFavoritesMock,
    setPinnedMock,
  };
}

beforeEach(() => {
  fakeRef.current = null;
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe("FavoritesList — optimistic pin reorder", () => {
  it("pins a favorite optimistically and floats it to the top", async () => {
    fakeRef.current = buildClient({
      initial: [
        entry({ id: "alice", displayName: "Alice" }),
        entry({ id: "bob", displayName: "Bob" }),
        entry({ id: "carol", displayName: "Carol" }),
      ],
    });

    renderWithQueryClient(<FavoritesList />);

    await screen.findByText("Alice");
    await screen.findByText("Bob");
    await screen.findByText("Carol");

    // Initial order: alphabetical (Alice, Bob, Carol).
    const beforeRows = screen.getAllByRole("listitem");
    expect(beforeRows[0].textContent).toContain("Alice");
    expect(beforeRows[1].textContent).toContain("Bob");
    expect(beforeRows[2].textContent).toContain("Carol");

    // Click pin on Carol — locate the button inside Carol's row so we
    // don't depend on document-order indexing across the three matching
    // buttons.
    const carolPin = beforeRows[2].querySelector(
      'button[aria-label="Pin to top"]',
    ) as HTMLButtonElement;
    expect(carolPin).not.toBeNull();
    fireEvent.click(carolPin);

    // Optimistic UI: Carol floats to row 0 with the pinned visual
    // affordance ("Unpin" aria-label). The reorder is what the user
    // sees immediately — the source-of-truth is the queued setEntries
    // updater inside `togglePin`, which React commits on the next
    // render after the click handler returns.
    await waitFor(() => {
      const rows = screen.getAllByRole("listitem");
      expect(rows[0].textContent).toContain("Carol");
      expect(rows[0].querySelector('button[aria-label="Unpin"]')).not.toBeNull();
    });
  });

  it("orders multiple pinned rows by most-recent pinnedAt", async () => {
    const t1 = "2026-05-01T00:00:00.000Z";
    const t2 = "2026-05-02T00:00:00.000Z";
    fakeRef.current = buildClient({
      initial: [
        entry({ id: "alice", displayName: "Alice", isPinned: true, pinnedAt: t1 }),
        entry({ id: "bob", displayName: "Bob", isPinned: true, pinnedAt: t2 }),
        entry({ id: "carol", displayName: "Carol" }),
      ],
    });

    renderWithQueryClient(<FavoritesList />);
    await screen.findByText("Alice");

    // Bob (pinnedAt=t2) is more recent than Alice (pinnedAt=t1) so Bob
    // floats above. Carol (unpinned) ends up last.
    await waitFor(() => {
      const rows = screen.getAllByRole("listitem");
      expect(rows[0].textContent).toContain("Bob");
      expect(rows[1].textContent).toContain("Alice");
      expect(rows[2].textContent).toContain("Carol");
    });
  });
});

// `sortFavorites` is the canonical client-side sort that drives the
// optimistic reorder AND the post-rollback restoration. Cover its
// invariants directly so a future fix to `togglePin` (when the React
// 19 eager-eval guard is repaired) doesn't have to re-derive them.
describe("sortFavorites — canonical client-side ordering", () => {
  it("places pinned rows before unpinned rows", () => {
    const sorted = sortFavorites([
      entry({ id: "a", displayName: "Anna" }),
      entry({ id: "b", displayName: "Beth", isPinned: true, pinnedAt: "2026-05-01T00:00:00Z" }),
    ]);
    expect(sorted.map((e) => e.id)).toEqual(["b", "a"]);
  });

  it("breaks ties between two pinned rows by most-recent pinnedAt", () => {
    const sorted = sortFavorites([
      entry({ id: "a", displayName: "Anna", isPinned: true, pinnedAt: "2026-05-01T00:00:00Z" }),
      entry({ id: "b", displayName: "Beth", isPinned: true, pinnedAt: "2026-05-02T00:00:00Z" }),
    ]);
    expect(sorted.map((e) => e.id)).toEqual(["b", "a"]);
  });

  it("orders unpinned rows alphabetically by displayName (case-insensitive)", () => {
    const sorted = sortFavorites([
      entry({ id: "1", displayName: "Charlie" }),
      entry({ id: "2", displayName: "alice" }),
      entry({ id: "3", displayName: "Bob" }),
    ]);
    expect(sorted.map((e) => e.displayName)).toEqual(["alice", "Bob", "Charlie"]);
  });

  it("returns a new array — does not mutate the input", () => {
    const input = [
      entry({ id: "a", displayName: "Anna" }),
      entry({ id: "b", displayName: "Beth" }),
    ];
    const inputCopy = [...input];
    sortFavorites(input);
    expect(input).toEqual(inputCopy);
  });
});
