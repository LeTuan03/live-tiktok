// N-room subscribe-only LiveKit manager backing the channel-ring feed.
//
// One coordinator per page; it holds a `Room` per channel returned by
// `GET /channels`. All rooms connect in parallel with `autoSubscribe: false`
// so the SFU never sends down media we won't render. Only the active room
// is subscribed (audio + video); `swipeNext()` / `swipePrev()` flip
// subscription on the previous and new active rooms — no room is ever
// reconnected.
//
// A follow-up will swap the inline `ChannelInfo` for the generated OpenAPI
// type; the field names match the server response so the migration is a
// one-line import change.

import {
  Room,
  RoomEvent,
  Track,
  type RemoteAudioTrack,
  type RemoteParticipant,
  type RemoteTrack,
  type RemoteTrackPublication,
  type RemoteVideoTrack,
} from "livekit-client";
import { PitchEventSource, type PitchEvent } from "./PitchEventSource";
import { LiveKitChatSource } from "@/lib/chat/LiveKitChatSource";
import type { ChatMessageSource, GlobalChatMessage } from "@/lib/chat/globalChat";

/**
 * Minimal channel shape consumed by the coordinator — matches the
 * `GET /channels` response. A follow-up will replace this with the generated
 * client type.
 *
 * `currentAd` is carried so the FeedPager has a synchronous source of
 * truth for the ad airing on each channel at bootstrap time. Without
 * it, the only way to surface a card on swipe is the per-swipe REST
 * `getCurrentAd` round-trip — and if that returns null (channel hasn't
 * logged its first ad yet, or transient REST failure) the user sees an
 * empty card on every swipe.
 */
export interface ChannelCurrentAdMedia {
  position: number;
  kind: string;
  url: string;
}

export interface ChannelCurrentAdProduct {
  id: string;
  title: string;
  priceCents: number | null;
  currency: string;
  imageUrl: string | null;
  deepLinkUrl: string | null;
  landingUrl: string | null;
  /** Ordered product-media gallery (image kind). Empty when the
   *  product has no `product_media` rows. */
  media: ChannelCurrentAdMedia[];
}

export interface ChannelCurrentAd {
  id: string;
  product: ChannelCurrentAdProduct;
  deepLinkUrl: string | null;
  startedAt: string;
  durationMs: number;
}

export interface ChannelInfo {
  id: string;
  livekitUrl: string;
  livekitToken: string;
  currentAd?: ChannelCurrentAd | null;
}

export type ChannelRoomCoordinatorEvent =
  | "active-track-changed"
  | "active-audio-track-changed"
  | "audio-playback-status-changed"
  | "active-pitch-event"
  | "active-chat-message";

export type ActiveTrackListener = (track: RemoteVideoTrack | null) => void;
export type ActiveAudioTrackListener = (track: RemoteAudioTrack | null) => void;
export type AudioPlaybackStatusListener = (canPlayback: boolean) => void;
export type ActivePitchListener = (event: PitchEvent) => void;
export type ActiveChatListener = (message: GlobalChatMessage) => void;

/** Bridge surface returned by `getActiveChatBridge()` — implements the
 *  read-only `ChatMessageSource` interface so it composes with the
 *  simulator on the consumer side, plus a `publish` method for the
 *  composer's outbound path. */
export interface ChatBridge extends ChatMessageSource {
  publish(handle: string, body: string): Promise<void>;
}

export interface ChannelRoomCoordinatorOptions {
  /** Test seam — defaults to a real LiveKit `Room` with adaptiveStream on. */
  roomFactory?: () => Room;
}

export class ChannelRoomCoordinator {
  private readonly roomFactory: () => Room;
  private readonly rooms: Room[] = [];
  private readonly channels: ChannelInfo[] = [];
  private readonly listeners = new Set<ActiveTrackListener>();
  private readonly audioListeners = new Set<ActiveAudioTrackListener>();
  private readonly audioStatusListeners = new Set<AudioPlaybackStatusListener>();
  private readonly pitchSources: PitchEventSource[] = [];
  private readonly pitchUnsubscribers: Array<() => void> = [];
  private readonly pitchListeners = new Set<ActivePitchListener>();
  private readonly chatSources: LiveKitChatSource[] = [];
  private readonly chatUnsubscribers: Array<() => void> = [];
  private readonly chatListeners = new Set<ActiveChatListener>();

  private active = 0;
  private activeTrack: RemoteVideoTrack | null = null;
  private activeAudioTrack: RemoteAudioTrack | null = null;
  private initialized = false;
  private disposed = false;

  constructor(options: ChannelRoomCoordinatorOptions = {}) {
    this.roomFactory =
      options.roomFactory ?? (() => new Room({ adaptiveStream: true, dynacast: false }));
  }

  get activeIndex(): number {
    return this.active;
  }

  get channelCount(): number {
    return this.rooms.length;
  }

  /**
   * Subscribe to active-video-track changes. Fires immediately with the
   * current track and again whenever the active room's video publication
   * binds or rebinds. Returns an unsubscribe fn — intended for a React
   * effect bridging the active track into element-attach state.
   */
  on(event: "active-track-changed", listener: ActiveTrackListener): () => void;
  on(event: "active-audio-track-changed", listener: ActiveAudioTrackListener): () => void;
  on(event: "audio-playback-status-changed", listener: AudioPlaybackStatusListener): () => void;
  on(event: "active-pitch-event", listener: ActivePitchListener): () => void;
  on(event: "active-chat-message", listener: ActiveChatListener): () => void;
  on(
    event: ChannelRoomCoordinatorEvent,
    listener:
      | ActiveTrackListener
      | ActiveAudioTrackListener
      | AudioPlaybackStatusListener
      | ActivePitchListener
      | ActiveChatListener,
  ): () => void {
    if (event === "active-track-changed") {
      const cb = listener as ActiveTrackListener;
      this.listeners.add(cb);
      cb(this.activeTrack);
      return () => {
        this.listeners.delete(cb);
      };
    }
    if (event === "active-audio-track-changed") {
      const cb = listener as ActiveAudioTrackListener;
      this.audioListeners.add(cb);
      cb(this.activeAudioTrack);
      return () => {
        this.audioListeners.delete(cb);
      };
    }
    if (event === "audio-playback-status-changed") {
      const cb = listener as AudioPlaybackStatusListener;
      this.audioStatusListeners.add(cb);
      cb(this.getCanPlaybackAudio());
      return () => {
        this.audioStatusListeners.delete(cb);
      };
    }
    if (event === "active-pitch-event") {
      const cb = listener as ActivePitchListener;
      this.pitchListeners.add(cb);
      return () => {
        this.pitchListeners.delete(cb);
      };
    }
    if (event === "active-chat-message") {
      const cb = listener as ActiveChatListener;
      this.chatListeners.add(cb);
      return () => {
        this.chatListeners.delete(cb);
      };
    }
    throw new Error(`ChannelRoomCoordinator: unsupported event "${event}"`);
  }

  /**
   * Return a read-only `ChatMessageSource` projection of the *active*
   * channel's chat traffic. Subscribers receive `GlobalChatMessage`s
   * for the channel the viewer is currently watching; swiping flips
   * the active source under the hood.
   *
   * Returned object also exposes `publish(handle, body)` so the
   * composer can fan-out to the rest of the room. We keep that method
   * off the base interface intentionally — the simulator is a
   * read-only source and shouldn't be forced to implement send.
   */
  getActiveChatBridge(): ChatBridge {
    return {
      subscribe: (listener) => this.on("active-chat-message", listener),
      publish: (handle, body) => this.publishActiveChat(handle, body),
      start: () => {},
      stop: () => {},
    };
  }

  /** Fire-and-forget chat publish to the current active channel's
   *  room. Resolves once the SFU has accepted the stream. No-op when
   *  no room is bound (between swipes, on a one-channel pager). */
  async publishActiveChat(handle: string, body: string): Promise<void> {
    if (this.disposed) return;
    const source = this.chatSources[this.active];
    if (!source) return;
    await source.publish(handle, body);
  }

  getActiveTrack(): RemoteVideoTrack | null {
    return this.activeTrack;
  }

  getActiveAudioTrack(): RemoteAudioTrack | null {
    return this.activeAudioTrack;
  }

  /**
   * Return the server-side `channels.id` for a channel ordinal — or
   * `null` if the ordinal is out of range / the coordinator hasn't been
   * initialized. Used by the FeedPager to fetch the currently-airing
   * ad via `getCurrentAd(channelId)` when the viewer joins mid-pitch,
   * since LiveKit text-streams aren't replayed for late joiners.
   */
  getChannelId(channelOrdinal: number): string | null {
    const info = this.channels[channelOrdinal];
    return info ? info.id : null;
  }

  /**
   * Return the bootstrap `currentAd` snapshot for a channel ordinal —
   * the ad that was airing when `GET /channels` was called at page
   * load. Used by the FeedPager to surface a card synchronously on
   * swipe, before the per-swipe `getCurrentAd` REST refresh resolves.
   * Stays defined for the lifetime of the coordinator; falls back to
   * the bootstrap value if a refresh returns null (e.g. transient REST
   * failure) so the user never loses the card mid-session.
   */
  getChannelCurrentAd(channelOrdinal: number): ChannelCurrentAd | null {
    const info = this.channels[channelOrdinal];
    if (!info) return null;
    return info.currentAd ?? null;
  }

  /**
   * Mirrors `Room.canPlaybackAudio` for the active room. When false the
   * browser's autoplay policy is blocking audio playback and the UI
   * needs to surface a "Tap to unmute" affordance.
   */
  getCanPlaybackAudio(): boolean {
    const room = this.rooms[this.active];
    if (!room) return true;
    const flag = (room as { canPlaybackAudio?: boolean }).canPlaybackAudio;
    return flag === undefined ? true : flag;
  }

  /**
   * Resume audio playback on the active room. Must be called inside a
   * user-gesture handler so the browser's transient activation flag
   * covers the underlying `audio.play()` call. Safe to invoke even when
   * `canPlaybackAudio` is already true — LiveKit no-ops in that case.
   */
  async startActiveAudio(): Promise<void> {
    const room = this.rooms[this.active];
    if (!room) return;
    const start = (room as { startAudio?: () => Promise<void> }).startAudio;
    if (typeof start !== "function") return;
    try {
      await start.call(room);
    } catch {
      // LK surfaces a rejection when the gesture has lapsed; the next
      // tap fires another startAudio. Swallow so the toggle still flips.
    }
  }

  /**
   * Synchronously register channel metadata so callers that need
   * ``getChannelCurrentAd`` / ``getChannelId`` *before* the async
   * ``init()`` resolves can read it. React runs a child component's
   * effects before its parent's, so ``FeedPager``'s mid-pitch seed
   * effect fires before ``ChannelsShell``'s ``init()`` effect — without
   * this hook the seed effect sees an empty channel array and the
   * bootstrap pinned-product card never materialises for a viewer who
   * joins mid-ad. Idempotent; safe to call before ``init()``.
   */
  prepareChannels(channels: ChannelInfo[]): void {
    if (this.disposed) return;
    if (this.channels.length > 0) return;
    if (channels.length === 0) return;
    this.channels.push(...channels);
  }

  /**
   * Connect N rooms (one per channel) in parallel with `autoSubscribe:
   * false` and bind subscription on the active room (`activeIndex = 0`).
   * Subsequent calls are a no-op — `dispose()` first if the channel list
   * changes.
   */
  async init(channels: ChannelInfo[]): Promise<void> {
    if (this.disposed) throw new Error("ChannelRoomCoordinator: already disposed");
    if (this.initialized) return;
    this.initialized = true;
    if (channels.length === 0) return;

    // ``prepareChannels`` may have already populated the metadata array
    // from the synchronous render path; only push if it hasn't.
    if (this.channels.length === 0) {
      this.channels.push(...channels);
    }
    for (let i = 0; i < channels.length; i++) {
      const room = this.roomFactory();
      this.rooms.push(room);
      this.bindRoomEvents(room, i);
      this.attachPitchSource(room, i);
      this.attachChatSource(room, i);
    }

    await Promise.all(
      channels.map((channel, i) =>
        this.rooms[i].connect(channel.livekitUrl, channel.livekitToken, {
          autoSubscribe: false,
        }),
      ),
    );

    if (this.disposed) return;
    this.applyActiveSubscriptions();
  }

  /** Advance the active index (modulo N) and flip subscriptions. */
  swipeNext(): void {
    if (this.rooms.length === 0) return;
    this.swipeTo((this.active + 1) % this.rooms.length);
  }

  /** Move back one channel (wrapping). */
  swipePrev(): void {
    if (this.rooms.length === 0) return;
    const n = this.rooms.length;
    this.swipeTo((this.active - 1 + n) % n);
  }

  async dispose(): Promise<void> {
    if (this.disposed) return;
    this.disposed = true;
    this.listeners.clear();
    this.audioListeners.clear();
    this.audioStatusListeners.clear();
    this.pitchListeners.clear();
    this.chatListeners.clear();
    this.activeTrack = null;
    this.activeAudioTrack = null;
    for (const unsub of this.pitchUnsubscribers.splice(0)) unsub();
    for (const source of this.pitchSources.splice(0)) source.dispose();
    for (const unsub of this.chatUnsubscribers.splice(0)) unsub();
    for (const source of this.chatSources.splice(0)) source.dispose();
    const rooms = this.rooms.splice(0, this.rooms.length);
    this.channels.length = 0;
    await Promise.all(
      rooms.map(async (room) => {
        room.removeAllListeners();
        try {
          await room.disconnect(true);
        } catch {
          // LK logs internally; disconnect errors are non-fatal here.
        }
      }),
    );
  }

  // MARK: - internals

  private swipeTo(next: number): void {
    if (this.disposed) return;
    if (next === this.active) return;

    const prev = this.active;
    this.active = next;

    this.setRoomSubscribed(this.rooms[prev], false);
    if (this.activeTrack !== null) {
      this.activeTrack = null;
      this.emitActiveTrack();
    }
    if (this.activeAudioTrack !== null) {
      this.activeAudioTrack = null;
      this.emitActiveAudioTrack();
    }
    this.setRoomSubscribed(this.rooms[next], true);
    this.refreshActiveTrackFromRoom(this.rooms[next]);
    this.emitAudioPlaybackStatus();
  }

  private applyActiveSubscriptions(): void {
    for (let i = 0; i < this.rooms.length; i++) {
      this.setRoomSubscribed(this.rooms[i], i === this.active);
    }
    this.refreshActiveTrackFromRoom(this.rooms[this.active]);
    this.emitAudioPlaybackStatus();
  }

  private setRoomSubscribed(room: Room, subscribed: boolean): void {
    for (const participant of room.remoteParticipants.values()) {
      for (const pub of participant.getTrackPublications()) {
        const remotePub = pub as RemoteTrackPublication;
        if (typeof remotePub.setSubscribed !== "function") continue;
        remotePub.setSubscribed(subscribed);
      }
    }
  }

  private refreshActiveTrackFromRoom(room: Room): void {
    let foundVideo = false;
    let foundAudio = false;
    for (const participant of room.remoteParticipants.values()) {
      for (const pub of participant.getTrackPublications()) {
        const remotePub = pub as RemoteTrackPublication;
        const track = remotePub.track;
        if (!track) continue;
        if (!foundVideo && track.kind === Track.Kind.Video) {
          this.activeTrack = track as RemoteVideoTrack;
          this.emitActiveTrack();
          foundVideo = true;
        } else if (!foundAudio && track.kind === Track.Kind.Audio) {
          this.activeAudioTrack = track as RemoteAudioTrack;
          this.emitActiveAudioTrack();
          foundAudio = true;
        }
        if (foundVideo && foundAudio) return;
      }
    }
  }

  /**
   * Attach a PitchEventSource for one room — the channel agent publishes
   * signal_product_pitch / signal_pitch_end on the `product_pitch`
   * text-stream topic, and viewers on *every* channel should receive
   * the same payload. Re-emit only for the active channel so the UI
   * pops the card on what the user is actually watching. Skipped when
   * the room (e.g. a unit-test fake) lacks the text-stream surface.
   */
  private attachPitchSource(room: Room, index: number): void {
    const source = new PitchEventSource();
    this.pitchSources.push(source);
    const unsub = source.subscribe((event) => {
      if (this.disposed) return;
      if (index !== this.active) return;
      for (const listener of this.pitchListeners) listener(event);
    });
    this.pitchUnsubscribers.push(unsub);
    if (this.supportsTextStreams(room)) {
      source.attach(room);
    }
  }

  /**
   * Attach a LiveKitChatSource for one room — same active-gated pattern
   * as the pitch source, for the global-chat text-stream (`global_chat`
   * topic). One source per channel so a swipe doesn't resubscribe to
   * LiveKit — only the active channel's messages re-emit to listeners,
   * but inbound text streams keep flowing on all rooms so the next
   * swipe doesn't drop the first few messages while the listener
   * re-attaches.
   */
  private attachChatSource(room: Room, index: number): void {
    const chatSource = new LiveKitChatSource();
    this.chatSources.push(chatSource);
    const chatUnsub = chatSource.subscribe((message) => {
      if (this.disposed) return;
      if (index !== this.active) return;
      for (const listener of this.chatListeners) listener(message);
    });
    this.chatUnsubscribers.push(chatUnsub);
    if (this.supportsTextStreams(room)) {
      chatSource.attach(room);
    }
  }

  /** True when the room exposes LiveKit's text-stream handler surface
   *  (absent on the unit-test fakes). */
  private supportsTextStreams(room: Room): boolean {
    return (
      typeof (room as { registerTextStreamHandler?: unknown }).registerTextStreamHandler ===
      "function"
    );
  }

  private bindRoomEvents(room: Room, index: number): void {
    room.on(RoomEvent.TrackPublished, (pub: RemoteTrackPublication, _p: RemoteParticipant) => {
      this.onTrackPublished(pub, index);
    });
    room.on(
      RoomEvent.TrackSubscribed,
      (track: RemoteTrack, _pub: RemoteTrackPublication, _p: RemoteParticipant) => {
        this.onTrackSubscribed(track, index);
      },
    );
    room.on(RoomEvent.TrackUnsubscribed, (track: RemoteTrack) => {
      this.onTrackUnsubscribed(track);
    });
    room.on(RoomEvent.AudioPlaybackStatusChanged, () => {
      this.onAudioPlaybackStatusChanged(index);
    });
  }

  private onTrackPublished(pub: RemoteTrackPublication, index: number): void {
    if (this.disposed) return;
    try {
      pub.setSubscribed(index === this.active);
    } catch {
      // LK logs internally.
    }
  }

  private onTrackSubscribed(track: RemoteTrack, index: number): void {
    if (this.disposed) return;
    if (index !== this.active) return;
    if (track.kind === Track.Kind.Video) {
      this.activeTrack = track as RemoteVideoTrack;
      this.emitActiveTrack();
    } else if (track.kind === Track.Kind.Audio) {
      this.activeAudioTrack = track as RemoteAudioTrack;
      this.emitActiveAudioTrack();
    }
  }

  private onTrackUnsubscribed(track: RemoteTrack): void {
    if (this.disposed) return;
    if (track.kind === Track.Kind.Video) {
      if (this.activeTrack === track) {
        this.activeTrack = null;
        this.emitActiveTrack();
      }
      return;
    }
    if (track.kind === Track.Kind.Audio) {
      if (this.activeAudioTrack === track) {
        this.activeAudioTrack = null;
        this.emitActiveAudioTrack();
      }
    }
  }

  // LiveKit takes an optimistic approach to autoplay — it tries
  // `audio.play()` on attach() and flips `room.canPlaybackAudio` to
  // false when the browser rejects. The UI mirrors that flag so it
  // can keep the unmute affordance visible even after the user has
  // tapped (mid-gesture lapses, sub-tab reactivation, etc.).
  private onAudioPlaybackStatusChanged(index: number): void {
    if (this.disposed) return;
    if (index !== this.active) return;
    this.emitAudioPlaybackStatus();
  }

  private emitActiveTrack(): void {
    for (const listener of this.listeners) listener(this.activeTrack);
  }

  private emitActiveAudioTrack(): void {
    for (const listener of this.audioListeners) listener(this.activeAudioTrack);
  }

  private emitAudioPlaybackStatus(): void {
    const flag = this.getCanPlaybackAudio();
    for (const listener of this.audioStatusListeners) listener(flag);
  }
}
