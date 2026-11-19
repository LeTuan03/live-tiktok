"use client";

import { useEffect, useRef, useState } from "react";
import {
  ConnectionState,
  DisconnectReason,
  Room,
  RoomEvent,
  Track,
  type RemoteTrack,
} from "livekit-client";

export type HostPhase = "idle" | "connecting" | "connected" | "lost" | "ended" | "error";

interface UseCustomHostRoomResult {
  videoTrack: RemoteTrack | null;
  audioTrack: RemoteTrack | null;
  phase: HostPhase;
  /** Mirrors `Room.canPlaybackAudio` — false while LK's autoplay gate blocks. */
  canPlaybackAudio: boolean;
  /** Call from inside a user gesture to resume audio playback. */
  startAudio: () => Promise<void>;
  /** Tell the agent the viewer's audio is live so it delivers the one-shot
   *  pitch now (the agent holds the opening turn until this arrives). */
  sendViewerReady: () => Promise<void>;
}

// Matches the server's `AGENT_CONTROL_TOPIC` (soshop.livekit.dispatch).
const AGENT_CONTROL_TOPIC = "agent_control";

/**
 * Subscriber-only connection to a customize preview room.
 *
 * A focused, subscriber-only room connection: it skips the viewer-token
 * round trip (the `/customize/host` response already carries `{ url, token }`)
 * and the data-channel envelopes (no product cards here), keeping only the
 * track subscription + connection lifecycle. Pass `null` for both args to stay
 * disconnected (edit mode).
 */
export function useCustomHostRoom(
  url: string | null,
  token: string | null,
): UseCustomHostRoomResult {
  const [videoTrack, setVideoTrack] = useState<RemoteTrack | null>(null);
  const [audioTrack, setAudioTrack] = useState<RemoteTrack | null>(null);
  const [phase, setPhase] = useState<HostPhase>("idle");
  const [canPlaybackAudio, setCanPlaybackAudio] = useState(true);
  const roomRef = useRef<Room | null>(null);
  // Mirrors "an agent audio track is live" for the viewer_ready retry loop,
  // read synchronously without waiting for a state re-render.
  const audioPresentRef = useRef(false);

  useEffect(() => {
    if (!url || !token) {
      setPhase("idle");
      return;
    }

    let cancelled = false;
    setPhase("connecting");
    setVideoTrack(null);
    setAudioTrack(null);
    audioPresentRef.current = false;

    // Subscriber-only: we never publish. Matches the feed's viewer room.
    const room = new Room({ adaptiveStream: true, dynacast: false });
    roomRef.current = room;

    room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
      if (track.kind === Track.Kind.Video) setVideoTrack(track);
      else if (track.kind === Track.Kind.Audio) {
        setAudioTrack(track);
        audioPresentRef.current = true;
      }
    });
    room.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
      if (track.kind === Track.Kind.Video) setVideoTrack((cur) => (cur === track ? null : cur));
      else if (track.kind === Track.Kind.Audio) {
        setAudioTrack((cur) => (cur === track ? null : cur));
        audioPresentRef.current = false;
      }
    });
    room.on(RoomEvent.ConnectionStateChanged, (state: ConnectionState) => {
      if (cancelled) return;
      if (state === ConnectionState.Connected) setPhase("connected");
      else if (state === ConnectionState.Reconnecting) setPhase("lost");
    });
    room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
      if (!cancelled) setCanPlaybackAudio(room.canPlaybackAudio);
    });
    room.on(RoomEvent.Disconnected, (reason?: DisconnectReason) => {
      if (cancelled) return;
      setPhase(reason === DisconnectReason.CLIENT_INITIATED ? "ended" : "lost");
    });

    void room
      .connect(url, token, { autoSubscribe: true })
      .then(() => {
        if (!cancelled) setCanPlaybackAudio(room.canPlaybackAudio);
      })
      .catch(() => {
        if (!cancelled) setPhase("error");
      });

    return () => {
      cancelled = true;
      roomRef.current = null;
      room.removeAllListeners();
      void room.disconnect(true);
    };
  }, [url, token]);

  const startAudio = async () => {
    try {
      await roomRef.current?.startAudio();
    } catch {
      // LK logs internally; swallow to avoid an unhandled rejection.
    }
  };

  const sendViewerReady = async () => {
    const room = roomRef.current;
    if (!room) return;
    const payload = new TextEncoder().encode(JSON.stringify({ action: "viewer_ready" }));
    // Data packets are not buffered, so a single send can race the agent's
    // data-handler registration (installed a few seconds into the dispatch)
    // and be silently dropped — leaving the agent to wait out its full
    // fallback timeout. Resend until the agent starts speaking (its audio
    // track arrives) or we hit the attempt cap, which stays under that
    // timeout. Duplicate viewer_ready signals are idempotent on the agent.
    for (let attempt = 0; attempt < 10; attempt++) {
      if (roomRef.current !== room || audioPresentRef.current) return;
      try {
        await room.localParticipant.publishData(payload, {
          reliable: true,
          topic: AGENT_CONTROL_TOPIC,
        });
      } catch {
        // Best-effort: the agent falls back to a timeout if this never lands.
      }
      await new Promise((resolve) => setTimeout(resolve, 1200));
    }
  };

  return { videoTrack, audioTrack, phase, canPlaybackAudio, startAudio, sendViewerReady };
}
