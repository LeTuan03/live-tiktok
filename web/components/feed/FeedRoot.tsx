"use client";
// Bootstrap for the channel-ring consumer feed.
//
// `/` is a public surface. On mount we hit `GET /channels`, which
// returns one entry per running channel pre-paired with a subscribe-only
// LiveKit token. The shell component (`ChannelsShell`) owns the
// `ChannelRoomCoordinator` lifecycle and renders the pager once the list
// is settled.
//
// The viewer-token grant minted by the server is `can_publish=False` and
// carries no dispatch metadata, so an anonymous browser never touches
// the agent loop.

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "@/lib/api";
import { listChannels, type ChannelInfo } from "@/lib/api/channels";
import { feedAnalytics } from "@/lib/analytics/FeedAnalyticsService";
import { useAgentSession } from "@/lib/agents/useAgentSession";
import { BootstrapOverlay } from "./BootstrapOverlay";
import { ChannelsShell } from "./ChannelsShell";
import { FeedShimmer } from "./FeedShimmer";
import { FeedErrorView } from "./FeedErrorView";

export function FeedRoot() {
  const [channels, setChannels] = useState<ChannelInfo[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [retryNonce, setRetryNonce] = useState(0);
  const { status: agentSession } = useAgentSession();

  useEffect(() => {
    const controller = new AbortController();
    (async () => {
      try {
        setError(null);
        const next = await listChannels(controller.signal);
        if (controller.signal.aborted) return;
        setChannels(next);
      } catch (err) {
        if (controller.signal.aborted) return;
        const name = (err as { name?: string } | null)?.name;
        if (name === "AbortError") return;
        const message =
          err instanceof ApiError ? err.detail : err instanceof Error ? err.message : String(err);
        setError(message);
        feedAnalytics.capture({
          kind: "feed_error",
          message,
        });
      }
    })();
    return () => {
      controller.abort();
    };
  }, [retryNonce]);

  const retry = useCallback(() => {
    feedAnalytics.capture({ kind: "feed_retry" });
    setError(null);
    setChannels(null);
    setRetryNonce((n) => n + 1);
  }, []);

  // Once the on-demand agent session flips to `active` we expect the
  // channels list to materialise within ~5 s (the server's reconcile
  // task finishes well before that). Refetch on the transition so the
  // empty-state overlay can hand off to the FeedPager cleanly — without
  // this the user would stare at the loading card forever even after
  // the rooms are up.
  const prevSessionActiveRef = useRef<boolean>(false);
  useEffect(() => {
    const active = agentSession?.active ?? false;
    const wasActive = prevSessionActiveRef.current;
    prevSessionActiveRef.current = active;
    if (active && !wasActive && channels !== null && channels.length === 0) {
      retry();
    }
  }, [agentSession?.active, channels, retry]);

  if (error) {
    return (
      <div className="fixed inset-0 z-40 bg-black">
        <FeedErrorView state={{ kind: "error", message: error }} onRetry={retry} />
      </div>
    );
  }

  if (channels === null) {
    return (
      <div className="fixed inset-0 z-40 bg-black">
        <FeedShimmer />
      </div>
    );
  }

  // `GET /channels` returning an empty array is a real state — no rooms
  // are currently airing. Same surface the user sees mid-warmup once
  // channels exist but no track has bound: blurred host placeholder
  // backdrop with the `BootstrapOverlay` running the cold-start state
  // machine on top. Cohabits with the FeedPager path so the user never
  // sees a contradictory "starting" vs. "live" surface across the empty
  // → channels-arrive transition.
  if (channels.length === 0) {
    // Cold-start surface: outer box paints the black letterbox bars,
    // inner column clamps to a 9:16 frame matching FeedPager's letterbox
    // column (see FeedPager.tsx — same `max-w-[calc(100dvh*9/16)]`
    // expression). On mobile portrait the viewport's width is narrower
    // than `100dvh * 9/16`, so the clamp never fires and the still fills
    // edge-to-edge. On desktop the clamp pins the column to a phone-
    // shaped frame with black bars on either side. The `<img>` uses
    // `object-cover` so the 9:16
    // still anchors center-bleed regardless of viewport aspect ratio.
    return (
      <div className="fixed inset-0 z-40 h-[100dvh] w-[100dvw] overflow-hidden bg-black">
        <div className="relative mx-auto h-full w-full max-w-[calc(100dvh*9/16)] overflow-hidden">
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img
            src="/personas/demo-host-9x16.png"
            alt=""
            aria-hidden
            data-testid="FeedRoot.emptyStateBackdrop"
            className="pointer-events-none absolute inset-0 h-full w-full object-cover object-center"
          />
          <BootstrapOverlay hasActiveTrack={false} />
        </div>
      </div>
    );
  }

  return <ChannelsShell channels={channels} />;
}
