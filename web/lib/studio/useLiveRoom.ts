"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { EMPTY_SNAPSHOT, LiveRoom, type LiveRoomSnapshot } from "./LiveRoom";
import type { LiveConnection } from "./types";

export interface UseLiveRoom {
  snapshot: LiveRoomSnapshot;
  sendChat: (name: string, text: string) => Promise<void>;
  startAudio: () => Promise<void>;
}

/**
 * Join the LiveKit room described by `connection` and leave it when the
 * connection changes or the component unmounts. Pass `null` to stay out of
 * the room (session not live yet, or ended).
 */
export function useLiveRoom(connection: LiveConnection | null): UseLiveRoom {
  const [snapshot, setSnapshot] = useState<LiveRoomSnapshot>(EMPTY_SNAPSHOT);
  const roomRef = useRef<LiveRoom | null>(null);

  const token = connection?.token ?? null;
  const livekitUrl = connection?.livekitUrl ?? null;
  const roomName = connection?.roomName ?? null;

  useEffect(() => {
    if (!token || !livekitUrl || !roomName) {
      setSnapshot(EMPTY_SNAPSHOT);
      return;
    }
    const room = new LiveRoom();
    roomRef.current = room;
    const unsubscribe = room.subscribe(setSnapshot);
    void room.connect({ token, livekitUrl, roomName });
    return () => {
      unsubscribe();
      if (roomRef.current === room) roomRef.current = null;
      void room.disconnect();
    };
  }, [token, livekitUrl, roomName]);

  const sendChat = useCallback(async (name: string, text: string) => {
    const room = roomRef.current;
    if (!room) throw new Error("Chưa kết nối phòng live.");
    await room.sendChat(name, text);
  }, []);

  const startAudio = useCallback(async () => {
    await roomRef.current?.startAudio();
  }, []);

  return { snapshot, sendChat, startAudio };
}
