import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type Room,
  RoomEvent,
  Track,
  type RemoteAudioTrack,
  type RemoteParticipant,
  type RemoteTrackPublication,
  type RemoteVideoTrack,
} from "livekit-client";
import { ChannelRoomCoordinator, type ChannelInfo } from "../ChannelRoomCoordinator";

type Listener = (...args: unknown[]) => void;

class FakePublication {
  readonly kind: Track.Kind;
  track: { kind: Track.Kind } | undefined;
  readonly setSubscribed = vi.fn<(subscribed: boolean) => void>((subscribed: boolean) => {
    this.subscribed = subscribed;
  });
  subscribed = false;

  constructor(kind: Track.Kind, options: { withTrack?: boolean } = {}) {
    this.kind = kind;
    if (options.withTrack ?? true) {
      this.track = { kind };
    }
  }
}

class FakeParticipant {
  constructor(private readonly publications: FakePublication[]) {}
  getTrackPublications(): FakePublication[] {
    return this.publications;
  }
}

class FakeRoom {
  private listeners = new Map<string, Set<Listener>>();
  readonly remoteParticipants = new Map<string, FakeParticipant>();
  canPlaybackAudio = true;
  readonly startAudio = vi.fn<() => Promise<void>>(async () => {
    this.canPlaybackAudio = true;
  });
  readonly connect = vi.fn<
    (url: string, token: string, options?: { autoSubscribe?: boolean }) => Promise<void>
  >(async () => undefined);
  readonly disconnect = vi.fn<() => Promise<void>>(async () => undefined);
  readonly removeAllListeners = vi.fn<() => void>(() => {
    this.listeners.clear();
  });

  on(event: string, listener: Listener): this {
    let set = this.listeners.get(event);
    if (!set) {
      set = new Set();
      this.listeners.set(event, set);
    }
    set.add(listener);
    return this;
  }

  emit(event: string, ...args: unknown[]): void {
    const set = this.listeners.get(event);
    if (!set) return;
    for (const listener of [...set]) listener(...args);
  }

  addParticipant(identity: string, publications: FakePublication[]): void {
    this.remoteParticipants.set(identity, new FakeParticipant(publications));
  }
}

function buildChannels(n: number): ChannelInfo[] {
  return Array.from({ length: n }, (_, i) => ({
    id: `ch-${i}`,
    livekitUrl: `wss://lk.example/${i}`,
    livekitToken: `token-${i}`,
  }));
}

function makeFakeRoom(): FakeRoom {
  return new FakeRoom();
}

interface Harness {
  coordinator: ChannelRoomCoordinator;
  rooms: FakeRoom[];
  pubs: FakePublication[][];
}

async function bootstrap(channelCount: number): Promise<Harness> {
  const rooms: FakeRoom[] = [];
  const pubs: FakePublication[][] = [];

  // Pre-allocate fakes — the factory hands one out per `init()` call.
  for (let i = 0; i < channelCount; i++) {
    const room = makeFakeRoom();
    const videoPub = new FakePublication(Track.Kind.Video);
    const audioPub = new FakePublication(Track.Kind.Audio);
    room.addParticipant(`agent-${i}`, [videoPub, audioPub]);
    rooms.push(room);
    pubs.push([videoPub, audioPub]);
  }

  let idx = 0;
  const coordinator = new ChannelRoomCoordinator({
    roomFactory: () => rooms[idx++] as unknown as Room,
  });
  await coordinator.init(buildChannels(channelCount));
  return { coordinator, rooms, pubs };
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe("ChannelRoomCoordinator", () => {
  it("connects all rooms in parallel with autoSubscribe: false", async () => {
    const { rooms } = await bootstrap(3);
    expect(rooms).toHaveLength(3);
    for (let i = 0; i < rooms.length; i++) {
      expect(rooms[i].connect).toHaveBeenCalledTimes(1);
      expect(rooms[i].connect).toHaveBeenCalledWith(`wss://lk.example/${i}`, `token-${i}`, {
        autoSubscribe: false,
      });
    }
  });

  it("subscribes only the active room after init (activeIndex=0)", async () => {
    const { coordinator, pubs } = await bootstrap(3);
    expect(coordinator.activeIndex).toBe(0);

    // Active room (0): both audio + video subscribed.
    for (const pub of pubs[0]) {
      expect(pub.setSubscribed).toHaveBeenCalledTimes(1);
      expect(pub.setSubscribed).toHaveBeenLastCalledWith(true);
    }
    // Inactive rooms (1, 2): publications explicitly unsubscribed.
    for (let i = 1; i < pubs.length; i++) {
      for (const pub of pubs[i]) {
        expect(pub.setSubscribed).toHaveBeenCalledTimes(1);
        expect(pub.setSubscribed).toHaveBeenLastCalledWith(false);
      }
    }
  });

  it("exposes the active video track via getActiveTrack()", async () => {
    const { coordinator, pubs } = await bootstrap(2);
    expect(coordinator.getActiveTrack()).toBe(pubs[0][0].track);
  });

  it("swipeNext() unsubscribes the previous room and subscribes the next", async () => {
    const { coordinator, pubs } = await bootstrap(3);

    // Reset history so we only assert on the swipe-driven calls.
    for (const channelPubs of pubs) {
      for (const pub of channelPubs) pub.setSubscribed.mockClear();
    }

    coordinator.swipeNext();
    expect(coordinator.activeIndex).toBe(1);

    // Previous active (0) gets unsubscribed for every publication.
    for (const pub of pubs[0]) {
      expect(pub.setSubscribed).toHaveBeenCalledTimes(1);
      expect(pub.setSubscribed).toHaveBeenCalledWith(false);
    }
    // New active (1) gets subscribed for every publication.
    for (const pub of pubs[1]) {
      expect(pub.setSubscribed).toHaveBeenCalledTimes(1);
      expect(pub.setSubscribed).toHaveBeenCalledWith(true);
    }
    // Untouched room (2) saw no further calls.
    for (const pub of pubs[2]) {
      expect(pub.setSubscribed).not.toHaveBeenCalled();
    }
  });

  it("swipeNext() unsubscribes before subscribing (correct sequence)", async () => {
    const { coordinator, pubs } = await bootstrap(2);

    const order: string[] = [];
    for (let i = 0; i < pubs.length; i++) {
      for (const pub of pubs[i]) {
        pub.setSubscribed.mockClear();
        pub.setSubscribed.mockImplementation((subscribed: boolean) => {
          order.push(`${i}:${pub.kind}:${subscribed ? "on" : "off"}`);
        });
      }
    }

    coordinator.swipeNext();

    // Previous-room unsubscribes happen before new-room subscribes.
    const firstSubscribeIdx = order.findIndex((entry) => entry.endsWith(":on"));
    const lastUnsubscribeIdx = order
      .map((entry, i) => ({ entry, i }))
      .filter(({ entry }) => entry.endsWith(":off"))
      .pop()?.i;
    expect(firstSubscribeIdx).toBeGreaterThan(-1);
    expect(lastUnsubscribeIdx).toBeDefined();
    expect(firstSubscribeIdx).toBeGreaterThan(lastUnsubscribeIdx as number);
  });

  it("swipePrev() wraps around from index 0 to N-1", async () => {
    const { coordinator, pubs } = await bootstrap(3);
    for (const channelPubs of pubs) {
      for (const pub of channelPubs) pub.setSubscribed.mockClear();
    }

    coordinator.swipePrev();
    expect(coordinator.activeIndex).toBe(2);

    for (const pub of pubs[0]) {
      expect(pub.setSubscribed).toHaveBeenCalledWith(false);
    }
    for (const pub of pubs[2]) {
      expect(pub.setSubscribed).toHaveBeenCalledWith(true);
    }
  });

  it("emits 'active-track-changed' to listeners on swipe", async () => {
    const { coordinator, rooms, pubs } = await bootstrap(2);

    const events: Array<RemoteVideoTrack | null> = [];
    const unsubscribe = coordinator.on("active-track-changed", (track) => {
      events.push(track);
    });
    // Listener fires once on registration with the current track.
    expect(events).toHaveLength(1);
    expect(events[0]).toBe(pubs[0][0].track);

    coordinator.swipeNext();
    // Swiping clears the active track immediately (room 1 hasn't fired
    // TrackSubscribed yet from the test's POV — the publication's `.track`
    // is also already set, so the coordinator picks it up synchronously).
    // Either way, the listener should observe at least one null OR the new
    // track. Assert the final emission is the new room's track once it
    // resolves via either path.
    const finalEvent = events[events.length - 1];
    expect(finalEvent).toBe(pubs[1][0].track);

    // Simulate a follow-up TrackSubscribed on the new active room — it
    // should keep the listener notified rather than discarding the event.
    const newTrack = { kind: Track.Kind.Video } as RemoteVideoTrack;
    pubs[1][0].track = newTrack;
    rooms[1].emit(
      RoomEvent.TrackSubscribed,
      newTrack,
      pubs[1][0] as unknown as RemoteTrackPublication,
      {} as RemoteParticipant,
    );
    expect(events[events.length - 1]).toBe(newTrack);

    unsubscribe();
    coordinator.swipeNext();
    // After unsubscribing the listener should not be called again.
    expect(events[events.length - 1]).toBe(newTrack);
  });

  it("TrackPublished after swipe subscribes only when the room is active", async () => {
    const { rooms, pubs } = await bootstrap(2);
    for (const channelPubs of pubs) {
      for (const pub of channelPubs) pub.setSubscribed.mockClear();
    }

    // Late-published video on the *inactive* room (index 1) must stay
    // unsubscribed.
    const latePubInactive = new FakePublication(Track.Kind.Video, {
      withTrack: false,
    });
    rooms[1].emit(
      RoomEvent.TrackPublished,
      latePubInactive as unknown as RemoteTrackPublication,
      {} as RemoteParticipant,
    );
    expect(latePubInactive.setSubscribed).toHaveBeenCalledTimes(1);
    expect(latePubInactive.setSubscribed).toHaveBeenCalledWith(false);

    // Late-published video on the *active* room (index 0) must subscribe.
    const latePubActive = new FakePublication(Track.Kind.Video, {
      withTrack: false,
    });
    rooms[0].emit(
      RoomEvent.TrackPublished,
      latePubActive as unknown as RemoteTrackPublication,
      {} as RemoteParticipant,
    );
    expect(latePubActive.setSubscribed).toHaveBeenCalledTimes(1);
    expect(latePubActive.setSubscribed).toHaveBeenCalledWith(true);
  });

  it("dispose() disconnects all rooms and clears listeners", async () => {
    const { coordinator, rooms } = await bootstrap(2);

    const listener = vi.fn<(t: RemoteVideoTrack | null) => void>();
    coordinator.on("active-track-changed", listener);
    listener.mockClear();

    await coordinator.dispose();

    for (const room of rooms) {
      expect(room.removeAllListeners).toHaveBeenCalledTimes(1);
      expect(room.disconnect).toHaveBeenCalledTimes(1);
      expect(room.disconnect).toHaveBeenCalledWith(true);
    }
    expect(coordinator.getActiveTrack()).toBeNull();
    expect(coordinator.channelCount).toBe(0);

    // No further emissions after dispose.
    expect(listener).not.toHaveBeenCalled();
  });

  it("init() is a no-op after dispose() (guards against late reconnect)", async () => {
    const { coordinator } = await bootstrap(2);
    await coordinator.dispose();
    await expect(coordinator.init(buildChannels(2))).rejects.toThrow(/already disposed/);
  });

  it("exposes the active audio track via getActiveAudioTrack()", async () => {
    const { coordinator, pubs } = await bootstrap(2);
    expect(coordinator.getActiveAudioTrack()).toBe(pubs[0][1].track);
  });

  it("emits 'active-audio-track-changed' on listener registration and swipe", async () => {
    const { coordinator, pubs } = await bootstrap(2);

    const events: Array<RemoteAudioTrack | null> = [];
    const unsubscribe = coordinator.on("active-audio-track-changed", (track) => {
      events.push(track);
    });
    expect(events).toHaveLength(1);
    expect(events[0]).toBe(pubs[0][1].track);

    coordinator.swipeNext();
    expect(events[events.length - 1]).toBe(pubs[1][1].track);

    unsubscribe();
    coordinator.swipeNext();
    expect(events[events.length - 1]).toBe(pubs[1][1].track);
  });

  it("startActiveAudio() calls room.startAudio() on the active room", async () => {
    const { coordinator, rooms } = await bootstrap(2);
    await coordinator.startActiveAudio();
    expect(rooms[0].startAudio).toHaveBeenCalledTimes(1);
    expect(rooms[1].startAudio).not.toHaveBeenCalled();

    coordinator.swipeNext();
    await coordinator.startActiveAudio();
    expect(rooms[1].startAudio).toHaveBeenCalledTimes(1);
  });

  it("startActiveAudio() swallows rejection so the toggle still flips", async () => {
    const { coordinator, rooms } = await bootstrap(1);
    rooms[0].startAudio.mockImplementationOnce(() => Promise.reject(new Error("autoplay blocked")));
    await expect(coordinator.startActiveAudio()).resolves.toBeUndefined();
  });

  it("prepareChannels() exposes getChannelCurrentAd / getChannelId before init() resolves", () => {
    // React mounts children's effects before parent's, so FeedPager's
    // seed effect fires before ChannelsShell calls init(). The seed
    // effect must still see the bootstrap `currentAd` snapshot —
    // otherwise a viewer refreshing the page mid-ad gets no pinned
    // product card. ChannelsShell calls prepareChannels() during
    // synchronous render to close that gap.
    const coordinator = new ChannelRoomCoordinator({
      roomFactory: () => makeFakeRoom() as unknown as Room,
    });
    const channels: ChannelInfo[] = [
      {
        id: "ch-0",
        livekitUrl: "wss://lk.example/0",
        livekitToken: "token-0",
        currentAd: {
          id: "ad-0",
          product: {
            id: "prod-0",
            title: "Bootstrap Pillow",
            priceCents: 5800,
            currency: "USD",
            imageUrl: null,
            deepLinkUrl: null,
            landingUrl: null,
            media: [],
          },
          deepLinkUrl: null,
          startedAt: "2024-01-01T00:00:00Z",
          durationMs: 15_000,
        },
      },
      {
        id: "ch-1",
        livekitUrl: "wss://lk.example/1",
        livekitToken: "token-1",
        currentAd: null,
      },
    ];

    coordinator.prepareChannels(channels);

    expect(coordinator.getChannelId(0)).toBe("ch-0");
    expect(coordinator.getChannelId(1)).toBe("ch-1");
    expect(coordinator.getChannelCurrentAd(0)?.product.id).toBe("prod-0");
    expect(coordinator.getChannelCurrentAd(1)).toBeNull();
  });

  it("prepareChannels() is idempotent and does not duplicate when init() runs after", async () => {
    const rooms = [makeFakeRoom(), makeFakeRoom()];
    let idx = 0;
    const coordinator = new ChannelRoomCoordinator({
      roomFactory: () => rooms[idx++] as unknown as Room,
    });
    const channels = buildChannels(2);

    coordinator.prepareChannels(channels);
    // A second prepareChannels call is a no-op.
    coordinator.prepareChannels(channels);
    await coordinator.init(channels);

    // No duplicates: channel count and id lookups still resolve cleanly.
    expect(coordinator.channelCount).toBe(2);
    expect(coordinator.getChannelId(0)).toBe("ch-0");
    expect(coordinator.getChannelId(1)).toBe("ch-1");
    expect(coordinator.getChannelId(2)).toBeNull();
  });

  it("emits 'audio-playback-status-changed' on RoomEvent.AudioPlaybackStatusChanged", async () => {
    const { coordinator, rooms } = await bootstrap(2);
    const events: boolean[] = [];
    coordinator.on("audio-playback-status-changed", (flag) => events.push(flag));
    // Listener fires once on registration with the current flag (true by default).
    expect(events).toEqual([true]);

    rooms[0].canPlaybackAudio = false;
    rooms[0].emit(RoomEvent.AudioPlaybackStatusChanged);
    expect(events[events.length - 1]).toBe(false);

    // Status change on the inactive room must NOT fire.
    rooms[1].canPlaybackAudio = false;
    const beforeLen = events.length;
    rooms[1].emit(RoomEvent.AudioPlaybackStatusChanged);
    expect(events.length).toBe(beforeLen);
  });
});
