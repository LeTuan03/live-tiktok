"use client";

import {
  ConnectionState,
  type LocalAudioTrack,
  type RemoteAudioTrack,
  type RemoteTrack,
  type RemoteTrackPublication,
  type RemoteVideoTrack,
  Room,
  type RoomConnectOptions,
  RoomEvent,
  type RoomOptions,
  Track,
  createLocalAudioTrack,
} from "livekit-client";

/**
 * Shared web wrapper around livekit-client's `Room`. Used by both the live
 * selling room (viewer-only, audio not published) and the 1-to-1 AskAvatar
 * surface (two-way audio with explicit mic permission).
 *
 * Responsibilities:
 *   * connect with a server-minted token
 *   * surface remote video/audio tracks to UI
 *   * publish/unpublish a local mic track on demand
 *   * disconnect cleanly on teardown
 *
 * Framework-agnostic on purpose. Components subscribe via `subscribe()`
 * (or React adapters in `useRoomClient.ts`) so the same instance can serve
 * both the live and AskAvatar surfaces without copy-paste.
 */

export type RoomClientState =
  | { kind: "idle" }
  | { kind: "connecting" }
  | { kind: "connected" }
  | { kind: "reconnecting" }
  | { kind: "disconnected" }
  | { kind: "failed"; error: string };

export interface RoomClientSnapshot {
  state: RoomClientState;
  remoteVideoTrack: RemoteVideoTrack | null;
  remoteAudioTrack: RemoteAudioTrack | null;
  micPublished: boolean;
}

export interface RoomClientOptions {
  /**
   * If true, the wrapper auto-publishes a local mic track on connect. Web
   * browsers gate `getUserMedia` behind a user gesture, so the AskAvatar
   * surface leaves this `false` and calls `publishMicrophone()` after the
   * user clicks the explicit "Start microphone" button. The live selling
   * surface leaves this `false` as well — viewers never publish.
   */
  autoPublishMic?: boolean;
  /** Pass-through to livekit-client. Defaults to `{ adaptiveStream: true, dynacast: true }`. */
  roomOptions?: RoomOptions;
  connectOptions?: RoomConnectOptions;
}

type Listener = (snapshot: RoomClientSnapshot) => void;

const DEFAULT_ROOM_OPTIONS: RoomOptions = {
  adaptiveStream: true,
  dynacast: true,
};

export class RoomClient {
  private readonly room: Room;
  private readonly autoPublishMic: boolean;
  private readonly connectOptions: RoomConnectOptions | undefined;

  private listeners = new Set<Listener>();
  private snapshot: RoomClientSnapshot = {
    state: { kind: "idle" },
    remoteVideoTrack: null,
    remoteAudioTrack: null,
    micPublished: false,
  };
  private localMic: LocalAudioTrack | null = null;
  private disposed = false;

  constructor(options: RoomClientOptions = {}) {
    this.autoPublishMic = options.autoPublishMic ?? false;
    this.connectOptions = options.connectOptions;
    this.room = new Room(options.roomOptions ?? DEFAULT_ROOM_OPTIONS);
    this.bindRoomEvents();
  }

  // ---------------------------------------------------------------- lifecycle

  async connect(url: string, token: string): Promise<void> {
    if (this.disposed) {
      throw new Error("RoomClient disposed; create a new instance");
    }
    if (
      this.room.state === ConnectionState.Connected ||
      this.room.state === ConnectionState.Connecting
    ) {
      return;
    }
    this.update({ state: { kind: "connecting" } });
    try {
      await this.room.connect(url, token, this.connectOptions);
      this.update({ state: { kind: "connected" } });
      this.refreshRemoteTracks();
      if (this.autoPublishMic) {
        await this.publishMicrophone();
      }
    } catch (err) {
      const message = err instanceof Error ? err.message : String(err);
      this.update({ state: { kind: "failed", error: message } });
      throw err;
    }
  }

  /**
   * Tear the room down. Idempotent — safe to call from a `useEffect`
   * cleanup function as well as from explicit "End session" UI.
   */
  async disconnect(): Promise<void> {
    if (this.disposed) return;
    this.disposed = true;
    try {
      if (this.localMic) {
        await this.unpublishMicrophone();
      }
      await this.room.disconnect();
    } finally {
      this.update({
        state: { kind: "disconnected" },
        remoteVideoTrack: null,
        remoteAudioTrack: null,
        micPublished: false,
      });
      this.listeners.clear();
    }
  }

  // --------------------------------------------------------------- microphone

  /**
   * Request mic permission (`getUserMedia`) and publish the local audio
   * track. Throws if the browser denies permission so callers can surface
   * a helpful error in the UI.
   */
  async publishMicrophone(): Promise<void> {
    if (this.disposed) return;
    if (this.localMic) {
      this.update({ micPublished: true });
      return;
    }
    const track = await createLocalAudioTrack({
      echoCancellation: true,
      noiseSuppression: true,
      autoGainControl: true,
    });
    this.localMic = track;
    await this.room.localParticipant.publishTrack(track, {
      source: Track.Source.Microphone,
    });
    this.update({ micPublished: true });
  }

  async unpublishMicrophone(): Promise<void> {
    if (!this.localMic) return;
    const track = this.localMic;
    this.localMic = null;
    try {
      await this.room.localParticipant.unpublishTrack(track, true);
    } finally {
      track.stop();
      this.update({ micPublished: false });
    }
  }

  // ----------------------------------------------------------------- snapshot

  getSnapshot(): RoomClientSnapshot {
    return this.snapshot;
  }

  subscribe(listener: Listener): () => void {
    this.listeners.add(listener);
    listener(this.snapshot);
    return () => {
      this.listeners.delete(listener);
    };
  }

  // ------------------------------------------------------------------ private

  private bindRoomEvents(): void {
    this.room.on(RoomEvent.TrackSubscribed, this.handleTrackSubscribed);
    this.room.on(RoomEvent.TrackUnsubscribed, this.handleTrackUnsubscribed);
    this.room.on(RoomEvent.ConnectionStateChanged, this.handleConnectionState);
    this.room.on(RoomEvent.Disconnected, this.handleDisconnected);
  }

  private handleTrackSubscribed = (track: RemoteTrack, _publication: RemoteTrackPublication) => {
    if (track.kind === Track.Kind.Video) {
      this.update({ remoteVideoTrack: track as RemoteVideoTrack });
    } else if (track.kind === Track.Kind.Audio) {
      this.update({ remoteAudioTrack: track as RemoteAudioTrack });
    }
  };

  private handleTrackUnsubscribed = (track: RemoteTrack, _publication: RemoteTrackPublication) => {
    if (track.kind === Track.Kind.Video) {
      if (this.snapshot.remoteVideoTrack?.sid === track.sid) {
        this.update({ remoteVideoTrack: null });
      }
    } else if (track.kind === Track.Kind.Audio) {
      if (this.snapshot.remoteAudioTrack?.sid === track.sid) {
        this.update({ remoteAudioTrack: null });
      }
    }
  };

  private handleConnectionState = (state: ConnectionState) => {
    switch (state) {
      case ConnectionState.Connecting:
        this.update({ state: { kind: "connecting" } });
        break;
      case ConnectionState.Connected:
        this.update({ state: { kind: "connected" } });
        break;
      case ConnectionState.Reconnecting:
        this.update({ state: { kind: "reconnecting" } });
        break;
      case ConnectionState.Disconnected:
        this.update({ state: { kind: "disconnected" } });
        break;
    }
  };

  private handleDisconnected = () => {
    this.update({
      state: { kind: "disconnected" },
      remoteVideoTrack: null,
      remoteAudioTrack: null,
      micPublished: false,
    });
  };

  private refreshRemoteTracks(): void {
    let video: RemoteVideoTrack | null = null;
    let audio: RemoteAudioTrack | null = null;
    for (const participant of this.room.remoteParticipants.values()) {
      for (const publication of participant.trackPublications.values()) {
        const track = publication.track;
        if (!track) continue;
        if (!video && track.kind === Track.Kind.Video) {
          video = track as RemoteVideoTrack;
        } else if (!audio && track.kind === Track.Kind.Audio) {
          audio = track as RemoteAudioTrack;
        }
      }
    }
    this.update({ remoteVideoTrack: video, remoteAudioTrack: audio });
  }

  private update(patch: Partial<RoomClientSnapshot>): void {
    this.snapshot = { ...this.snapshot, ...patch };
    for (const listener of this.listeners) {
      listener(this.snapshot);
    }
  }
}
