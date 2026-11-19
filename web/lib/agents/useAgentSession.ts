"use client";
// Shared React Query bindings for the on-demand agent session.
//
// `POST /api/agents/session` mints (or extends) an agent session — see
// `server/src/soshop/api/routes/agents.py`. The session is process-
// wide, so any client that knows it's been started must see the same
// snapshot. Centralising the query key + mutation in one hook keeps the
// SoShop wordmark trigger (`SoShopLogoButton`) and the cold-start
// overlay (`BootstrapOverlay`) reading from the same React Query cache:
// hitting either surface flips both at once and prevents the two from
// drifting into contradictory "active / not active" reads.

import { useEffect } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  fetchAgentSessionStatus,
  startAgentSession,
  type AgentSessionStatus,
} from "@/lib/agents/session-client";

export const AGENT_SESSION_QUERY_KEY = ["agent-session"] as const;

const INACTIVE_STATUS: AgentSessionStatus = {
  active: false,
  expiresAt: null,
  secondsRemaining: 0,
  targetChannelCount: 0,
};

export interface UseAgentSessionResult {
  status: AgentSessionStatus | undefined;
  /** `true` while the bootstrap POST is in flight — the overlay swaps
   *  to its loading layout under this flag. */
  isStarting: boolean;
  /** The most recent start-mutation error, if any. Cleared by the next
   *  successful POST. */
  startError: Error | null;
  /** Fire-and-forget kick to `POST /api/agents/session`. Bound to a
   *  user-gesture handler — the underlying mutation owns its own
   *  retry/error state. */
  start: () => void;
}

export function useAgentSession(): UseAgentSessionResult {
  const qc = useQueryClient();

  // Active sessions tick at 5s so the wordmark countdown chip stays
  // honest; idle sessions back off to 60s — the cache is the
  // authoritative read for "did anyone else just start this," and we
  // do not want N tabs hammering /api/agents/session every 5s while
  // nothing is happening.
  const query = useQuery<AgentSessionStatus>({
    queryKey: AGENT_SESSION_QUERY_KEY,
    queryFn: fetchAgentSessionStatus,
    refetchInterval: (q) => (q.state.data?.active ? 5_000 : 60_000),
  });

  const mutation = useMutation({
    mutationFn: startAgentSession,
    onSuccess: (next) => {
      qc.setQueryData(AGENT_SESSION_QUERY_KEY, next);
    },
  });

  // Server-side expiry happens on `_expires_at < now`; the next 5s
  // poll would catch it, but during that lag the overlay sees
  // `hasActiveTrack=false` + a stale `active=true` and renders the
  // warming card instead of returning to the "Sell me stuff" CTA.
  // Flip the cache to inactive locally the moment `expiresAt` passes
  // so the BootstrapOverlay + countdown chip transition immediately.
  const expiresAt = query.data?.expiresAt ?? null;
  useEffect(() => {
    if (!expiresAt) return;
    const flip = () => {
      qc.setQueryData<AgentSessionStatus>(AGENT_SESSION_QUERY_KEY, (prev) => {
        // Only flip if this effect's deadline still matches the cache —
        // a fresh start() that pushed `expiresAt` forward must win.
        if (!prev || prev.expiresAt !== expiresAt) return prev;
        return INACTIVE_STATUS;
      });
    };
    const ms = new Date(expiresAt).getTime() - Date.now();
    if (ms <= 0) {
      flip();
      return;
    }
    // `setTimeout` clamps anything above ~24.8 days (2^31-1 ms) and fires
    // it immediately. Real sessions expire in minutes, but a malformed
    // payload or far-future test fixture would otherwise flip the cache
    // straight back to inactive. Skip those — polling will catch the
    // (improbable) case where the session legitimately outlives that.
    const MAX_TIMEOUT_MS = 2_147_483_647;
    if (ms > MAX_TIMEOUT_MS) return;
    const id = setTimeout(flip, ms);
    return () => clearTimeout(id);
  }, [expiresAt, qc]);

  return {
    status: query.data,
    isStarting: mutation.isPending,
    startError: mutation.error,
    start: () => mutation.mutate(),
  };
}
