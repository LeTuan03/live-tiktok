"use client";

// LiveKit room wrapper for the studio control room and the public viewer
// page. One instance per join. It surfaces:
//   * the model's video and audio tracks (the agent publishes one of each)
//   * whether the browser currently allows audio playback
//   * chat on the `live_chat` text-stream topic
//   * live state pushed by the agent on the `live_state` topic
//
// Framework-agnostic: React reads it through `useLiveRoom`.

import {
  type RemoteAudioTrack,
  type RemoteTrack,
  type RemoteVideoTrack,
  Room,
  RoomEvent,
  Track,
} from "livekit-client";
import { adaptLiveState, type WireLiveState } from "./api";
import type { ChatMessage, LiveConnection, LiveState } from "./types";

export const LIVE_CHAT_TOPIC = "live_chat";
export const LIVE_STATE_TOPIC = "live_state";

const MAX_CHAT_MESSAGES = 200;

export type LiveRoomStatus =
  | "idle"
  | "connecting"
  | "connected"
  | "reconnecting"
  | "disconnected"
  | "failed";

export interface LiveRoomSnapshot {
  status: LiveRoomStatus;
  error: string | null;
  videoTrack: RemoteVideoTrack | null;
  audioTrack: RemoteAudioTrack | null;
  canPlayAudio: boolean;
  chat: ChatMessage[];
  liveState: LiveState | null;
}

export const EMPTY_SNAPSHOT: LiveRoomSnapshot = {
  status: "idle",
  error: null,
  videoTrack: null,
  audioTrack: null,
  canPlayAudio: true,
  chat: [],
  liveState: null,
};

type Listener = (snapshot: LiveRoomSnapshot) => void;

/** Parse a `live_chat` payload. Returns null for anything that is not a
 *  `{name, text}` object with a non-empty text. */
export function parseChatPayload(raw: string): { name: string; text: string } | null {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  if (!data || typeof data !== "object") return null;
  const { name, text } = data as { name?: unknown; text?: unknown };
  const body = typeof text === "string" ? text.trim() : "";
  if (!body) return null;
  const author = typeof name === "string" && name.trim() ? name.trim() : "Khách";
  return { name: author, text: body };
}

/** Parse a `live_state` payload pushed by the agent. A push without a
 *  timestamp is stamped with the receive time so it can be ordered against
 *  the polled state. */
export function parseLiveStatePayload(raw: string, now: () => Date = () => new Date()) {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return null;
  }
  const state = adaptLiveState(data as WireLiveState);
  if (!state) return null;
  return state.updatedAt ? state : { ...state, updatedAt: now().toISOString() };
}

export class LiveRoom {
  private readonly room: Room;
  private readonly listeners = new Set<Listener>();
  private snapshot: LiveRoomSnapshot = EMPTY_SNAPSHOT;
  private disposed = false;
  private counter = 0;

  constructor(roomFactory: () => Room = () => new Room({ adaptiveStream: true, dynacast: true })) {
    this.room = roomFactory();
    this.bind();
  }

  getSnapshot(): LiveRoomSnapshot {
    return this.snapshot;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => {
      this.listeners.delete(listener);
    };
  }

  async connect(connection: LiveConnection): Promise<void> {
    if (this.disposed) return;
    this.update({ status: "connecting", error: null });
    try {
      await this.room.connect(connection.livekitUrl, connection.token);
      if (this.disposed) return;
      this.update({ status: "connected", canPlayAudio: this.room.canPlaybackAudio });
      this.refreshTracks();
    } catch (err) {
      if (this.disposed) return;
      const message = err instanceof Error ? err.message : String(err);
      this.update({ status: "failed", error: message });
    }
  }

  /** Must run inside a user gesture: unlocks audio after an autoplay block. */
  async startAudio(): Promise<void> {
    if (this.disposed) return;
    try {
      await this.room.startAudio();
    } catch {
      // The gesture lapsed; the next tap retries.
    }
    this.update({ canPlayAudio: this.room.canPlaybackAudio });
  }

  /** Publish a chat line. LiveKit does not echo a text stream back to its
   *  sender, so the message is added to the local list once the send
   *  resolves. Throws when the send fails so the caller can show it. */
  async sendChat(name: string, text: string): Promise<void> {
    const body = text.trim();
    if (!body || this.disposed) return;
    const author = name.trim() || "Khách";
    await this.room.localParticipant.sendText(JSON.stringify({ name: author, text: body }), {
      topic: LIVE_CHAT_TOPIC,
    });
    this.pushChat(author, body, true, this.room.localParticipant.identity ?? "me");
  }

  async disconnect(): Promise<void> {
    if (this.disposed) return;
    this.disposed = true;
    for (const topic of [LIVE_CHAT_TOPIC, LIVE_STATE_TOPIC]) {
      try {
        this.room.unregisterTextStreamHandler(topic);
      } catch {
        // Already unregistered.
      }
    }
    this.room.removeAllListeners();
    try {
      await this.room.disconnect();
    } catch {
      // livekit-client logs transport errors itself.
    }
    this.snapshot = {
      ...this.snapshot,
      status: "disconnected",
      videoTrack: null,
      audioTrack: null,
    };
    this.listeners.clear();
  }

  // ------------------------------------------------------------------ private

  private bind(): void {
    const room = this.room;
    room.on(RoomEvent.TrackSubscribed, (track: RemoteTrack) => {
      if (track.kind === Track.Kind.Video) {
        this.update({ videoTrack: track as RemoteVideoTrack });
      } else if (track.kind === Track.Kind.Audio) {
        this.update({ audioTrack: track as RemoteAudioTrack });
      }
    });
    room.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
      if (this.snapshot.videoTrack === track) this.update({ videoTrack: null });
      if (this.snapshot.audioTrack === track) this.update({ audioTrack: null });
      // Another track of the same kind may still be published.
      this.refreshTracks();
    });
    room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
      this.update({ canPlayAudio: room.canPlaybackAudio });
    });
    room.on(RoomEvent.Reconnecting, () => this.update({ status: "reconnecting" }));
    room.on(RoomEvent.Reconnected, () => {
      this.update({ status: "connected" });
      this.refreshTracks();
    });
    room.on(RoomEvent.Disconnected, () => {
      this.update({ status: "disconnected", videoTrack: null, audioTrack: null });
    });

    room.registerTextStreamHandler(LIVE_CHAT_TOPIC, async (reader, info) => {
      try {
        const parsed = parseChatPayload(await reader.readAll());
        if (parsed) this.pushChat(parsed.name, parsed.text, false, info?.identity ?? "anon");
      } catch {
        // A truncated stream is dropped; chat is best effort.
      }
    });
    room.registerTextStreamHandler(LIVE_STATE_TOPIC, async (reader) => {
      try {
        const state = parseLiveStatePayload(await reader.readAll());
        if (state) this.update({ liveState: state });
      } catch {
        // Polling covers a missed push.
      }
    });
  }

  private pushChat(name: string, text: string, mine: boolean, identity: string): void {
    this.counter += 1;
    const at = Date.now();
    const message: ChatMessage = { id: `${identity}:${at}:${this.counter}`, name, text, mine, at };
    this.update({ chat: [...this.snapshot.chat, message].slice(-MAX_CHAT_MESSAGES) });
  }

  private refreshTracks(): void {
    let video = this.snapshot.videoTrack;
    let audio = this.snapshot.audioTrack;
    for (const participant of this.room.remoteParticipants.values()) {
      for (const publication of participant.trackPublications.values()) {
        const track = publication.track;
        if (!track) continue;
        if (!video && track.kind === Track.Kind.Video) video = track as RemoteVideoTrack;
        if (!audio && track.kind === Track.Kind.Audio) audio = track as RemoteAudioTrack;
      }
    }
    if (video !== this.snapshot.videoTrack || audio !== this.snapshot.audioTrack) {
      this.update({ videoTrack: video, audioTrack: audio });
    }
  }

  private update(patch: Partial<LiveRoomSnapshot>): void {
    if (this.disposed) return;
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) listener(this.snapshot);
  }
}
