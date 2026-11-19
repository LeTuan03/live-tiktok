"use client";
// Connects the bootstrap channel list to the live `FeedPager`.
//
// `FeedRoot` owns the `GET /channels` fetch + loading/error UI; once a
// stable list is in hand it mounts `ChannelsShell` with the array, and
// this component owns the `ChannelRoomCoordinator` lifecycle: build it,
// kick off the parallel room connect, render the pager once channels
// are wired, and dispose on unmount.

import { useEffect, useRef } from "react";
import { ChannelRoomCoordinator } from "@/lib/feed/ChannelRoomCoordinator";
import type { ChannelInfo } from "@/lib/api/channels";
import { FeedPager } from "./FeedPager";

interface ChannelsShellProps {
  channels: ChannelInfo[];
}

interface CoordinatorSlot {
  coordinator: ChannelRoomCoordinator;
  channels: ChannelInfo[];
  disposeTimer: ReturnType<typeof setTimeout> | null;
}

/**
 * Lazily build (or reuse) the coordinator slot for the current channel
 * list. Disposes the prior coordinator when the channel array identity
 * changes. Pure with respect to React — operates on the passed ref, so
 * it is safe to call unconditionally during render.
 */
function ensureCoordinatorSlot(
  slotRef: { current: CoordinatorSlot | null },
  channels: ChannelInfo[],
): CoordinatorSlot {
  const existing = slotRef.current;
  if (existing !== null && existing.channels === channels) {
    return existing;
  }
  if (existing) {
    if (existing.disposeTimer) clearTimeout(existing.disposeTimer);
    void existing.coordinator.dispose();
  }
  const coordinator = new ChannelRoomCoordinator();
  // Stash channel metadata synchronously so FeedPager's mid-pitch
  // seed effect — which fires before the async `init()` below — can
  // pin the bootstrap `currentAd` card for a viewer who refreshes
  // the page mid-ad.
  coordinator.prepareChannels(channels);
  const slot: CoordinatorSlot = { coordinator, channels, disposeTimer: null };
  slotRef.current = slot;
  return slot;
}

export function ChannelsShell({ channels }: ChannelsShellProps) {
  // The coordinator owns N live LiveKit websockets — it must survive
  // React 19 StrictMode's mount → cleanup → mount double-invoke in dev.
  // A naive `useMemo` + dispose-on-cleanup pair tears the rooms down
  // before they finish connecting and the immediate re-mount can't
  // recover (init() is idempotent on a disposed coordinator and the
  // silent `.catch()` swallows the "already disposed" throw). We keep
  // the instance in a ref so its identity is stable across both
  // invocations, and defer the dispose so the re-mount can cancel it.
  const slotRef = useRef<CoordinatorSlot | null>(null);
  const coordinator = ensureCoordinatorSlot(slotRef, channels).coordinator;

  useEffect(() => {
    const slot = slotRef.current;
    if (!slot) return;
    if (slot.disposeTimer) {
      clearTimeout(slot.disposeTimer);
      slot.disposeTimer = null;
    }
    void coordinator.init(channels).catch(() => {
      // `Room.connect` failures surface as rejections here. The player
      // already renders a shimmer while no track binds, so the user
      // sees feedback; the coordinator logs the underlying LK error
      // internally. A user reload is the higher-priority retry path.
    });
    return () => {
      slot.disposeTimer = setTimeout(() => {
        void coordinator.dispose();
        slot.disposeTimer = null;
      }, 0);
    };
  }, [coordinator, channels]);

  return <FeedPager coordinator={coordinator} channelCount={channels.length} />;
}
