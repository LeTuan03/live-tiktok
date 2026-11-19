"use client";

import Link from "next/link";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { useInfiniteQuery, useMutation, useQueryClient } from "@tanstack/react-query";

import { ApiError } from "@/lib/api";
import {
  DefaultFavoritesAPIClient,
  type FavoriteEntry,
  type FavoritesAPIClient,
  type FavoritesPage,
  sortFavorites,
} from "@/lib/favorites/FavoritesAPIClient";
import { favoritesKeys } from "@/lib/favorites/queryKeys";
import { FavoritesEmptyState } from "./EmptyState";

// MARK: - Data hook (TanStack-backed)

interface UseFavoritesResult {
  entries: FavoriteEntry[];
  status: "idle" | "loading" | "loaded" | "error";
  errorMessage: string | null;
  hasMore: boolean;
  isFetchingMore: boolean;
  pinInFlight: ReadonlySet<string>;
  reload(): void;
  loadMore(): void;
  togglePin(personaId: string): void;
}

/**
 * Favorites view-model backed by TanStack Query's `useInfiniteQuery`
 * for pagination and `useMutation` (with optimistic update + rollback)
 * for the pin toggle.
 *
 * Cross-surface invalidation: any mutation site (persona profile, feed
 * card heart) calls `queryClient.invalidateQueries({ queryKey:
 * favoritesKeys.list() })`; this list refetches on its next mount
 * (because of `staleTime: 30s`) or immediately if it's already mounted
 * (TanStack handles the subscription).
 */
function useFavorites(client: FavoritesAPIClient): UseFavoritesResult {
  const queryClient = useQueryClient();

  const query = useInfiniteQuery({
    queryKey: favoritesKeys.list(),
    queryFn: ({ pageParam }) => client.fetchFavorites({ cursor: pageParam ?? null }),
    initialPageParam: null as string | null,
    getNextPageParam: (lastPage: FavoritesPage) => lastPage.nextCursor ?? null,
  });

  // Flatten paginated pages → sorted entries. We re-sort across pages so a
  // newly-pinned entry from page 2 floats into the pinned section above
  // page-1 unpinned rows, matching the server's canonical sort.
  const entries = useMemo<FavoriteEntry[]>(() => {
    if (!query.data) return [];
    const flat = query.data.pages.flatMap((p) => p.favorites);
    return sortFavorites(flat);
  }, [query.data]);

  const pinMutation = useMutation({
    mutationFn: async ({ personaId, pinned }: { personaId: string; pinned: boolean }) => {
      await client.setPinned(personaId, pinned);
    },
    onMutate: async ({ personaId, pinned }) => {
      // Cancel in-flight refetches so the optimistic write isn't
      // immediately stomped by a slow server response.
      await queryClient.cancelQueries({ queryKey: favoritesKeys.list() });
      const snapshot = queryClient.getQueryData(favoritesKeys.list());

      queryClient.setQueryData<{
        pages: FavoritesPage[];
        pageParams: (string | null)[];
      }>(favoritesKeys.list(), (prev) => {
        if (!prev) return prev;
        return {
          ...prev,
          pages: prev.pages.map((page) => ({
            ...page,
            favorites: page.favorites.map((e) =>
              e.id === personaId
                ? {
                    ...e,
                    isPinned: pinned,
                    pinnedAt: pinned ? new Date().toISOString() : null,
                  }
                : e,
            ),
          })),
        };
      });

      return { snapshot };
    },
    onError: (_err, _vars, ctx) => {
      // Roll back optimistic write on failure.
      if (ctx?.snapshot !== undefined) {
        queryClient.setQueryData(favoritesKeys.list(), ctx.snapshot);
      }
    },
    onSettled: () => {
      void queryClient.invalidateQueries({ queryKey: favoritesKeys.list() });
    },
  });

  const togglePin = useCallback(
    (personaId: string) => {
      const entry = entries.find((e) => e.id === personaId);
      if (!entry) return;
      pinMutation.mutate({ personaId, pinned: !entry.isPinned });
    },
    [entries, pinMutation],
  );

  // Mirror prior status semantics so the view branches don't change.
  const status: UseFavoritesResult["status"] = query.isPending
    ? "loading"
    : query.isError
      ? "error"
      : "loaded";

  const errorMessage =
    query.error == null
      ? null
      : query.error instanceof ApiError
        ? query.error.detail
        : query.error instanceof Error
          ? query.error.message
          : "Failed to load favorites";

  const pinInFlight = useMemo<ReadonlySet<string>>(() => {
    const pending = new Set<string>();
    if (pinMutation.isPending && pinMutation.variables) {
      pending.add(pinMutation.variables.personaId);
    }
    return pending;
  }, [pinMutation.isPending, pinMutation.variables]);

  return {
    entries,
    status,
    errorMessage,
    hasMore: query.hasNextPage ?? false,
    isFetchingMore: query.isFetchingNextPage,
    pinInFlight,
    reload: () => {
      void query.refetch();
    },
    loadMore: () => {
      if (!query.hasNextPage || query.isFetchingNextPage) return;
      void query.fetchNextPage();
    },
    togglePin,
  };
}

// MARK: - View

/**
 * Favorites surface. The backend is open access, so the list always
 * renders.
 */
export function FavoritesList() {
  // Stable client across renders. The backend is open access, so it
  // carries no auth token.
  const client = useMemo<FavoritesAPIClient>(() => new DefaultFavoritesAPIClient(), []);

  const {
    entries,
    status,
    errorMessage,
    hasMore,
    isFetchingMore,
    pinInFlight,
    reload,
    loadMore,
    togglePin,
  } = useFavorites(client);

  if (status === "idle" || status === "loading") {
    return <SkeletonList />;
  }
  if (status === "error") {
    return <ErrorState message={errorMessage} onRetry={reload} />;
  }
  if (entries.length === 0) {
    return <FavoritesEmptyState />;
  }

  return (
    <section className="mx-auto max-w-2xl px-4 py-6">
      <h1 className="mb-4 text-2xl font-semibold">Favorites</h1>
      <ul className="flex flex-col gap-2">
        {entries.map((entry) => (
          <li key={entry.id} style={viewTransitionStyle(entry.id)}>
            <FavoriteRow
              entry={entry}
              pinInFlight={pinInFlight.has(entry.id)}
              onTogglePin={() => togglePin(entry.id)}
            />
          </li>
        ))}
      </ul>
      {hasMore ? (
        <InfiniteScrollSentinel onIntersect={loadMore} isLoading={isFetchingMore} />
      ) : null}
    </section>
  );
}

// MARK: - Row

/**
 * Compact list-variant of the persona card used on Feed. If a shared
 * `<PersonaCard variant="compact" />` lands later, this private row
 * should adopt it.
 */
function FavoriteRow({
  entry,
  pinInFlight,
  onTogglePin,
}: {
  entry: FavoriteEntry;
  pinInFlight: boolean;
  onTogglePin: () => void;
}) {
  return (
    <div
      className={[
        "flex items-center gap-3 rounded-xl border px-3 py-2.5 transition-colors",
        entry.isPinned
          ? "border-brand-accent/50 bg-white/[0.08]"
          : "border-transparent bg-white/[0.04]",
      ].join(" ")}
    >
      <Link
        href={`/persona/${encodeURIComponent(entry.id)}`}
        className="flex flex-1 items-center gap-3"
      >
        <FavoriteAvatar url={entry.avatarUrl} alt={entry.displayName} />
        <div className="flex min-w-0 flex-col">
          <span className="truncate text-base font-bold">{entry.displayName}</span>
          <span className="truncate text-sm text-brand-muted">{entry.handle}</span>
        </div>
      </Link>
      <button
        type="button"
        onClick={onTogglePin}
        disabled={pinInFlight}
        aria-label={entry.isPinned ? "Unpin" : "Pin to top"}
        aria-pressed={entry.isPinned}
        className={[
          "ml-auto inline-flex h-11 w-11 items-center justify-center rounded-full",
          "text-lg font-bold transition-opacity disabled:opacity-40",
          entry.isPinned ? "text-brand-accent" : "text-brand-muted",
        ].join(" ")}
      >
        {entry.isPinned ? "📌" : "📍"}
      </button>
    </div>
  );
}

function FavoriteAvatar({ url, alt }: { url: string | null; alt: string }) {
  if (!url) {
    return (
      <div
        aria-hidden
        className="h-12 w-12 flex-none rounded-full bg-gradient-to-br from-brand-accent to-brand"
      />
    );
  }
  return (
    // eslint-disable-next-line @next/next/no-img-element
    <img src={url} alt={alt} className="h-12 w-12 flex-none rounded-full object-cover" />
  );
}

// MARK: - Infinite scroll sentinel

/**
 * Sentinel element that fires `onIntersect` whenever it scrolls into
 * the viewport. The hook intentionally does not debounce — `loadMore`
 * itself is idempotent (TanStack guards against duplicate fetchNextPage
 * calls), so a chatty observer is harmless.
 */
function InfiniteScrollSentinel({
  onIntersect,
  isLoading,
}: {
  onIntersect: () => void;
  isLoading: boolean;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const callbackRef = useRef(onIntersect);
  useEffect(() => {
    callbackRef.current = onIntersect;
  }, [onIntersect]);

  useEffect(() => {
    const node = ref.current;
    if (!node) return;
    if (typeof IntersectionObserver === "undefined") return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) callbackRef.current();
        }
      },
      { rootMargin: "200px" },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  return (
    <div ref={ref} className="flex justify-center py-6">
      {isLoading ? (
        <span className="text-sm text-brand-muted">Loading more…</span>
      ) : (
        <span className="sr-only">Load more</span>
      )}
    </div>
  );
}

// MARK: - View transitions

/**
 * Tag each row with a unique `view-transition-name` so browsers that
 * support CSS view transitions animate the pin reorder smoothly. On
 * unsupported browsers the property is ignored and the reorder is
 * still flicker-free because React's keyed list reuses the same DOM
 * nodes across the sort change.
 */
function viewTransitionStyle(id: string): React.CSSProperties {
  return { viewTransitionName: `favorite-${cssId(id)}` } as React.CSSProperties;
}

function cssId(id: string): string {
  return id.replace(/[^a-zA-Z0-9_-]/g, "_");
}

// MARK: - Loading / error states

function SkeletonList() {
  return (
    <section className="mx-auto max-w-2xl px-4 py-6" aria-busy="true">
      <h1 className="mb-4 text-2xl font-semibold">Favorites</h1>
      <ul className="flex flex-col gap-2">
        {Array.from({ length: 6 }).map((_, i) => (
          <li key={i} className="h-16 animate-pulse rounded-xl bg-white/[0.06]" />
        ))}
      </ul>
    </section>
  );
}

function ErrorState({ message, onRetry }: { message: string | null; onRetry: () => void }) {
  return (
    <section className="mx-auto flex min-h-[60dvh] max-w-md flex-col items-center justify-center gap-3 px-6 text-center">
      <h2 className="text-lg font-bold">Couldn&apos;t load favorites</h2>
      {message ? <p className="text-sm text-brand-muted">{message}</p> : null}
      <button
        type="button"
        onClick={onRetry}
        className="rounded-full bg-brand-accent px-5 py-2 text-sm font-semibold text-white"
      >
        Retry
      </button>
    </section>
  );
}
