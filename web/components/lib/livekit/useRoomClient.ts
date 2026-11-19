"use client";

import { useEffect, useMemo, useState } from "react";
import { RoomClient, type RoomClientOptions, type RoomClientSnapshot } from "./RoomClient";

/**
 * React adapter for `RoomClient`. Owns the wrapper's lifetime — the
 * instance is created on mount and `disconnect()` is invoked on unmount,
 * which is what satisfies the "clean teardown when the user
 * navigates away" criterion (Next.js calls cleanup on route change).
 */
export function useRoomClient(options: RoomClientOptions = {}): {
  client: RoomClient;
  snapshot: RoomClientSnapshot;
} {
  // Stable across renders — the cleanup is keyed on this exact instance.
  // We deliberately ignore changes to `options` after the first render; if
  // a caller needs to reconfigure, they should remount the component.
  const client = useMemo(() => new RoomClient(options), []); // eslint-disable-line react-hooks/exhaustive-deps

  const [snapshot, setSnapshot] = useState<RoomClientSnapshot>(() => client.getSnapshot());

  useEffect(() => {
    const unsubscribe = client.subscribe(setSnapshot);
    return () => {
      unsubscribe();
      void client.disconnect();
    };
  }, [client]);

  return { client, snapshot };
}
