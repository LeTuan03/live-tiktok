"use client";

// App-wide TanStack Query client.
//
// Mounted once in the root layout (`app/layout.tsx`). Every fetching
// surface — favorites, channels, persona profile, partner analytics —
// reads through `useQuery` / `useMutation` so cross-surface
// invalidation lands automatically when, e.g., the persona-profile
// heart toggles and the favorites list is open in the background.
//
// We don't enable streaming SSR hydration on the consumer feed: the
// existing surfaces fetch on mount (anonymous-by-default per the
// `(consumer)` route group), so a client-side provider is sufficient
// for v1. Partner surfaces also bootstrap on
// mount. Bring hydration in when a route's TTFB is dominated by API
// latency.
//
// Defaults:
//   - `staleTime: 30s` — most server data churns slowly; this avoids
//     the React 19 + StrictMode double-mount refetch storm in dev.
//   - `gcTime: 5min` — keep dismounted queries around long enough to
//     survive a route switch and a return.
//   - `retry: 1` — one quiet retry on network failures, then surface
//     the error to the caller's UI.

import { useState, type ReactNode } from "react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";

function makeQueryClient(): QueryClient {
  return new QueryClient({
    defaultOptions: {
      queries: {
        staleTime: 30_000,
        gcTime: 5 * 60_000,
        retry: 1,
        refetchOnWindowFocus: true,
        refetchOnReconnect: true,
      },
      mutations: {
        retry: 0,
      },
    },
  });
}

export function AppQueryClientProvider({ children }: { children: ReactNode }) {
  // Lazy-construct so each browser tab gets its own client. `useState`
  // (rather than module-scope) keeps the provider safe under React
  // StrictMode's mount → unmount → mount dance in dev — the client
  // survives the cleanup tick because the state holder is stable.
  const [client] = useState(makeQueryClient);
  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
