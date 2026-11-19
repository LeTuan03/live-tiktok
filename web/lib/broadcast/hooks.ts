"use client";

// React glue for broadcasts: the server snapshot (query + live event stream)
// and a ticking clock for the derived on-air position.

import { type QueryClient, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState } from "react";
import { ApiError } from "@/lib/api";
import { eventsUrl, getBroadcast, newerBroadcast, parseBroadcastEvent } from "./api";
import { broadcastKeys } from "./queryKeys";
import type { Broadcast } from "./types";

/** Store a snapshot unless the cache already holds a newer one. */
export function storeBroadcast(qc: QueryClient, broadcast: Broadcast): void {
  qc.setQueryData<Broadcast>(broadcastKeys.detail(broadcast.id), (prev) =>
    newerBroadcast(prev, broadcast),
  );
}

/**
 * The broadcast, kept current by `GET /broadcasts/{id}/events` (server-sent
 * events). While the stream is down the query polls every 4 s instead.
 */
export function useBroadcast(id: string) {
  const qc = useQueryClient();
  const [streaming, setStreaming] = useState(false);

  const query = useQuery({
    queryKey: broadcastKeys.detail(id),
    queryFn: () => getBroadcast(id),
    staleTime: 0,
    retry: (count, err) => !(err instanceof ApiError && err.status === 404) && count < 2,
    refetchInterval: streaming ? false : 4000,
  });

  useEffect(() => {
    if (typeof EventSource === "undefined") return;
    let source: EventSource;
    try {
      source = new EventSource(eventsUrl(id));
    } catch {
      return;
    }
    source.onmessage = (event) => {
      const snapshot = parseBroadcastEvent(event.data, Date.now());
      if (snapshot) {
        storeBroadcast(qc, snapshot);
        setStreaming(true);
      }
    };
    source.addEventListener("gone", () => {
      void qc.invalidateQueries({ queryKey: broadcastKeys.detail(id) });
    });
    // EventSource reconnects by itself; poll meanwhile.
    source.onerror = () => setStreaming(false);
    return () => source.close();
  }, [id, qc]);

  return query;
}

/** `Date.now()`, refreshed every `intervalMs` while mounted. */
export function useNow(intervalMs = 250): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    const timer = window.setInterval(() => setNow(Date.now()), intervalMs);
    return () => window.clearInterval(timer);
  }, [intervalMs]);
  return now;
}
